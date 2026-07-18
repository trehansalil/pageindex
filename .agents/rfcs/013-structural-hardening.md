<!-- Space: CITRA -->
<!-- Title: RFC-013: Structural Hardening Batch — Performance, Error Handling, Corpus Quality -->
<!-- Folder: RFCs -->

---
id: RFC-013
title: Structural Hardening Batch — Performance, Error Handling, Corpus Quality
status: proposed
date: 2026-07-16
plan-impact: yes
supersedes-decisions-in: []
---

## Context

`audit/DOCSTORE_AUDIT_REPORT.md`'s Batch 2 (structural, 1-2 day estimate) covers
performance (unbounded serial I/O), error-handling hygiene, and two corpus-quality
gaps (OCR script-mismatch, garble-gate floor/duplication) that need a code fix plus a
corpus re-validation pass before landing. Re-verified 2026-07-16: three items already
fixed (ISS-08, ISS-18, ISS-19), leaving four open items batched here because they
share a theme — production-load-bearing paths that degrade silently rather than fail
loud — even though their code surfaces don't overlap.

### What this RFC covers

| Issue | Status | File:Line | One-liner |
|---|---|---|---|
| ISS-08 | **Already fixed** | `converters.py:1316-1369` | `_describe` retries + counts OpenAI errors by type |
| ISS-18 | **Already fixed** | `helpers.py:62-76`, `:120` | JSON-parse except narrowed, shared `_extract_json_object` |
| ISS-19 | **Already fixed** | `helpers.py:221-223` | Narrowed except + `RAG_PARSE_FAILURES` counter |
| ISS-05 | Open | `storage.py:420-423` | `list_processed_docs` does one serial MinIO GET per doc — O(N) wall-clock |
| ISS-44 | Open | `tools/documents.py:~352`, `client.py:~769` | Page-hit extraction logic duplicated across two call sites |
| ISS-34 | Open — corpus-quality | `converters.py:719-752` | Missing non-Latin tessdata silently drops to `["deu","eng"]` instead of raising |
| ISS-36 | Open — corpus-quality | `helpers.py:535`, `:1072` | Garble-gate digit-ratio check has a 500-char floor; `_tree_is_garbled`/`_flat_text_is_garbled` still duplicated post-RFC-010 |

### What this RFC does NOT cover

- **ISS-05's long-term fix** (Approach B: registry-only listing, no MinIO GET at all)
  — explicitly gated on ISS-03 (already fixed) plus a stable registry, tracked as
  audit Batch 3 long-term work, not this RFC. This RFC ships the bounded-concurrency
  interim (Approach C).
- **ISS-36's new sub-500-char ratio threshold** — audit and verification agent both
  flag this as false-positive risk (the corpus was burned once already by the
  GHV-TKV-Tarif wide-table false positive on pipe/€ symbols). This RFC only
  deduplicates the two garble functions into one; it does not invent a new heuristic.
- Corpus re-validation itself (re-running the full corpus through the pipeline after
  ISS-36 lands) — required before ISS-36 can be considered "done," but that
  re-validation run is an operational step, not part of this RFC's code changes.
  Tracked as a follow-up task gated on this RFC merging.

## Hard Rule constraints (CLAUDE.md — binding)

- **HR5** — ISS-34 and ISS-36 both touch the garbling-detection path that feeds
  `validate_tree()`. Neither loosens the gate: ISS-34 makes a silent-failure path
  *raise* instead of silently degrading (tightening); ISS-36 deduplicates existing
  logic without changing its thresholds (behavior-preserving).
- **HR4** — none of these fixes touch the AGPL/pymupdf surface.

## Decision

### D1-D3 — ISS-08, ISS-18, ISS-19: no code change, close as resolved

- ISS-08: `_describe` (`converters.py:1316-1369`) already retries transient OpenAI
  errors with backoff and increments `IMAGE_DESCRIBE_FAILURES.labels(error_type=...)`
  at both retry-exhausted and permanent-failure branches.
- ISS-18/19: `_extract_json_object` (`helpers.py:62-76`) is shared by both call sites;
  `_search_one_doc` narrows its except to `(json.JSONDecodeError, KeyError, TypeError)`
  and increments `RAG_PARSE_FAILURES.labels(doc_id=doc_id)`. `_prefilter_docs` narrows
  its except the same way but still only logs a warning with no counter — a minor gap
  the audit didn't flag; noted here, not actioned (out of scope, audit only asked for
  a counter on ISS-19 specifically).

No implementation required beyond marking all three closed in the audit tracker.

### D4 — ISS-05: bounded-concurrency MinIO fetch for `list_processed_docs`

`storage.py:420-423` issues one synchronous `mc.get_object` per doc, serially.
Interim fix (Approach C — cuts wall-clock without eliminating the O(N) call count):

```python
sem = asyncio.Semaphore(10)
async def _fetch(doc_id, obj_name):
    async with sem:
        return await asyncio.to_thread(mc.get_object, settings.minio_bucket, obj_name)
results = await asyncio.gather(
    *(_fetch(d, o) for d, o in meta_keys.items()), return_exceptions=True
)
```

`list_processed_docs` is currently sync — this requires either making it `async` or
wrapping the call site (`client.py:286`) in `asyncio.to_thread`. The registry-only
long-term fix (Approach B) is tracked separately per "What this RFC does NOT cover."

### D5 — ISS-44: extract shared page-hit helper

`tools/documents.py` (~352-360) and `client.py` (~769-776) independently implement
the same page-spec parse (`"1-3,5"` → `set[int]`) and node-filter logic; only
`_build_node_map` is currently shared. Extract to `helpers.py`:

```python
def _extract_page_hits(structure: list, pages: str) -> list[dict]:
    wanted = _parse_page_spec(pages)
    nm: dict = {}
    _build_node_map(structure, nm)
    return [n for n in nm.values() if _node_pages(n) & wanted]
```

Both call sites replace their inline loop with `hits = _extract_page_hits(structure, pages)`,
keeping their own logging/metrics wrapper around the call — this is the same pattern
the codebase already uses for `_build_node_map`.

### D6 — ISS-34: raise on missing non-Latin tessdata instead of silent drop

`converters.py:719-752` drops any requested language missing from the tessdata
directory with a `logger.warning`, and if `available` ends up empty, silently returns
`["deu", "eng"]` regardless of what script was actually requested. This is the
confirmed root cause of the مرسوم-13 Latin-mojibake-passes-garble-gate failure mode
(see memory `fix3-ocr-escalation-mojibake-escape`). Define a `TessdataUnavailableError`;
raise it when a *non-Latin* requested language (i.e., not in a defined Latin-script set)
is unavailable, rather than silently substituting:

```python
class TessdataUnavailableError(RuntimeError):
    pass

# inside ensure_tessdata / language-resolution loop:
if lang not in _LATIN_LANGS and lang not in available:
    raise TessdataUnavailableError(f"non-Latin tessdata missing: {lang}")
```

`client.py:472` feeds `ensure_tessdata`'s return straight into
`pdf_to_markdown_docling(..., langs)`; `client.py:492-496` already has an
`except Exception → OCR_ESCALATION_TOTAL.labels(result="error")` branch that preserves
the pre-escalation garbled state, so the raise lands cleanly on existing error
handling — `low_quality_tree` surfaces correctly instead of persisting false-clean
mojibake. Companion infra item (tracked separately, not code): pre-bake `ara.traineddata`
so the raise doesn't become the common case for Arabic corpora.

### D7 — ISS-36: deduplicate garble-detection into one shared function

`_tree_is_garbled` (`helpers.py:535`) and `_flat_text_is_garbled` (`helpers.py:1072`)
independently implement the same digit-ratio (>0.60, gated on `len(blob) > 500`) and
token-repetition (>0.30, gated on `len(tokens) > 20`) checks. RFC-010's D3/D3B landed
the token-repetition guard into *both* separately — exactly the fix-one-miss-the-other
drift this issue warns about. Extract one shared `_is_garbled_blob(text) -> bool` and
have both call it:

```python
def _is_garbled_blob(text: str) -> bool:
    # existing digit-ratio + token-repetition logic, unified
    ...

def _tree_is_garbled(structure) -> bool:
    return _is_garbled_blob(_flatten_tree_text(structure))

def _flat_text_is_garbled(text: str) -> bool:
    return _is_garbled_blob(text)
```

Thresholds and the 500-char/20-token floors are preserved exactly — this is a pure
dedup, not a heuristic change (per "What this RFC does NOT cover"). Corpus
re-validation is required after this lands to confirm no behavior drift, tracked as a
follow-up operational task.

## Implementation Plan

1. D4 (ISS-05, ~15 lines) — independent, ship anytime
2. D5 (ISS-44, ~15 lines) — independent, ship anytime
3. D6 (ISS-34, ~15 lines) — pairs with the `ara.traineddata` pre-bake infra item for
   full effect, but the raise itself is independently correct and should ship first
4. D7 (ISS-36, ~15 lines) — ship, then run the corpus re-validation follow-up before
   declaring done

D1-D3 (ISS-08/18/19) require no implementation — mark resolved in the audit tracker.

## Test Strategy

| Decision | Test |
|---|---|
| D4 | Assert `list_processed_docs` issues fetches under the semaphore bound (mock `mc.get_object` call count/concurrency) |
| D5 | Parametrized test asserting `tools/documents.py` and `client.py` page-hit results are identical for the same `(structure, pages)` input post-extraction |
| D6 | `test_converters_contract.py`: assert `TessdataUnavailableError` raises on `ensure_tessdata(["ara"])` with prefix set + file absent + download off (currently only the drop path is tested) |
| D7 | `test_rfc010_helpers.py`: add a short-numeric-junk (≤500 char, >60% digit) parametrized case run through both `_tree_is_garbled` and `_flat_text_is_garbled`, asserting they agree |

## Risks

- D6 changes a silent-degrade path into a raise — any deployment currently relying on
  the eng/deu fallback for non-Latin OCR (even accidentally) will start seeing
  `low_quality_tree` errors until `ara.traineddata` (or the relevant script) is
  pre-baked. Sequence the tessdata pre-bake alongside this fix, not after.
- D7 requires a full corpus re-validation pass before close-out — budget that as a
  distinct operational step, not assume "tests pass" is sufficient sign-off given the
  prior GHV-TKV-Tarif false-positive history.
