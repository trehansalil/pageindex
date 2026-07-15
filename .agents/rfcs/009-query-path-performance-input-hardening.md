<!-- Space: CITRA -->
<!-- Title: RFC-009: Query-Path Performance & Input Hardening -->
<!-- Parent: RFCs -->
<!-- Confluence-Page-Id: 5091819550 -->

<!-- Confluence-Page-ID: 5091819550 -->

<!-- Confluence-URL: https://inheaden.atlassian.net/wiki/spaces/CITRA/pages/5091819550/RFC-009+Query-Path+Performance+Input+Hardening -->

---

id: RFC-009
title: Query-Path Performance & Input Hardening
status: landed
date: 2026-07-10
plan-impact: yes
supersedes-decisions-in: []
---------------------------

## Context

The docstore audit (Wave 3, 2026-07-10) surfaced 5 issues sharing a systemic pattern:
**O(N) fallback paths** — MinIO listing is O(N) with serial GETs, and this path fires on
every registry fallback AND on every "document not found" error, creating both a
performance bottleneck and a DoS vector. Two additional issues address input-validation
gaps (unbounded uploads, unverified tessdata downloads) that compound the risk surface.

RFC-006 introduced a Postgres-backed document registry (`list_docs()` via single SQL
query) to replace O(N) MinIO listing. This RFC addresses the remaining paths where the
old MinIO-listing codepath is still reachable and the input-validation holes that were
never closed.

### What this RFC covers

| Issue  | Severity | File:Line                          | One-liner                                                     |
| ------ | -------- | ---------------------------------- | ------------------------------------------------------------- |
| ISS-05 | DEGRADED | `storage.py:392-429`             | `list_processed_docs` O(N) serial MinIO GETs                |
| ISS-06 | DEGRADED | `tools/documents.py:74,109-122`  | `recent_documents` fetches all docs then slices client-side |
| ISS-14 | LATENT   | `converters.py:755-768`          | Tessdata download with no integrity verification              |
| ISS-15 | LATENT   | `upload_app.py:89`               | Upload endpoint has no file size limit                        |
| ISS-21 | LATENT   | `tools/documents.py:195,258,300` | Error paths trigger O(N) MinIO listing — DoS vector          |

### What this RFC does NOT cover

- Registry backfill strategy — covered by RFC-006 D1/D3.
- Redis singleton lifecycle — covered by RFC-008 (ISS-07).
- Registry dual-write correctness — covered by RFC-007 (ISS-03).
- The tree-walk search step — already O(candidate-set), not O(corpus).

## Hard Rule constraints (CLAUDE.md — binding)

- **HR1** — no fix is framed as beating vector RAG on accuracy. All changes are
  performance/security, not retrieval quality.
- **HR2** — no fix adds a new persisted artifact type. `node_count` is added to the
  existing `.meta.json` sidecar, not a new file.
- **HR5** — the ISS-05 sidecar enrichment writes `node_count` inside `save_doc_meta()`
  which already runs after `validate_tree()`. No new store path is introduced.

## Decision

### D1 — Remove O(N) listing from error paths (ISS-21) — immediate

**Problem.** Three MCP tools — `get_document` (`documents.py:195`), `get_document_structure`
(`documents.py:258`), `get_page_content` (`documents.py:300`) — call
`list_processed_docs()` on every invalid `doc_id` solely to populate an `available` list
in the error response. This triggers N sequential MinIO GETs per bad request.

**Decision.** Return `{"error": "Document not found: {doc_id}"}` without calling
`list_processed_docs()`. The MCP tool description already says "Use recent_documents()
to find available doc_ids" — the `available` array is redundant guidance that costs O(N)
to produce.

**Rationale.** Pure code removal. No behavioral change for well-behaved clients. Eliminates
the DoS vector where an attacker (or buggy client) flooding with invalid doc_ids triggers
N MinIO GETs per request.

```python
# BEFORE (documents.py:195, :258, :300):
available = [d["doc_id"] for d in list_processed_docs()]
return json.dumps({"error": f"Document not found: {doc_id}", "available": available})

# AFTER:
return json.dumps({"error": f"Document not found: {doc_id}"})
```

### D2 — Store `node_count` in `.meta.json` sidecar at save time (ISS-05 short-term)

**Problem.** `recent_documents` (`documents.py:113-122`) deserializes the full tree for
every page item just to count nodes:

```python
data = get_doc(doc_id)           # full tree from MinIO or Redis cache
_build_node_map(data.get("structure", []), nm)
node_count = len(nm)
```

A 10-page listing deserializes 10 full trees. For large documents (100k+ chars), this is
the dominant cost of the `recent_documents` call even on the registry path.

**Decision.** Compute `node_count` in `save_doc_meta()` (`storage.py`) at ingestion time
and persist it in the `.meta.json` sidecar. `recent_documents` reads `node_count` from
the listing metadata instead of deserializing trees. The registry schema already has room
for this column (add `node_count INTEGER` to the `documents` table, populated by
dual-write).

**Rationale.** Ingestion runs once per document; `recent_documents` runs on every page
view. Moving the work to write-time amortizes it. The O(N) MinIO GETs in
`list_processed_docs()` remain but each response is now tiny metadata — the per-doc tree
deserialization loop in `recent_documents` is eliminated entirely.

### D3 — Server-side pagination for `recent_documents` (ISS-06)

**Problem.** `_list_docs_with_fallback()` (`documents.py:74`) fetches up to 100,000 rows
from the registry, then `recent_documents` slices in Python:

```python
docs = await list_docs(limit=100_000, offset=0)   # fetch all
page_docs = docs[begin : begin + page_size]        # slice in Python
```

At 10k documents, page-1 request fetches all 10k rows only to display 10.

**Decision.** Pass `limit=page_size, offset=(page-1)*page_size` directly to `list_docs()`
on the registry path. The registry's `list_docs` already supports `LIMIT`/`OFFSET`
parameters — they are simply not being used. On the MinIO fallback path, the existing
fetch-all-then-slice behavior is retained (the fallback is already degraded; optimizing
it is not worth the complexity — it goes away entirely with D6).

**Dependencies.** D2 (sidecar `node_count`) to eliminate the tree deserialization loop.
ISS-07 (RFC-008) for Redis singleton to avoid connection churn on the cache path.

### D4 — Chunked upload with size limit (ISS-15)

**Problem.** `upload_app.py:89` reads the entire uploaded file into memory in one call:

```python
file_bytes = await file.read()  # no Content-Length check, no streaming, no max-size
```

An authenticated client can crash the server with a multi-GB upload.

**Decision.** Replace unbounded `file.read()` with chunked read (1 MB chunks). Abort with
HTTP 413 if total exceeds `MAX_UPLOAD_SIZE_MB` (new env var, default 100 MB). The
existing API key gate limits the attack surface to authenticated clients, but a size limit
is defense-in-depth.

**New config:** `MAX_UPLOAD_SIZE_MB` (int, default `100`). Added to `settings.py` alongside
existing `PAGEINDEX_*` env vars.

```python
# Sketch:
MAX_SIZE = int(os.environ.get("MAX_UPLOAD_SIZE_MB", "100")) * 1024 * 1024
chunks = []
total = 0
while chunk := await file.read(1_048_576):  # 1 MB
    total += len(chunk)
    if total > MAX_SIZE:
        raise HTTPException(status_code=413, detail=f"File exceeds {MAX_UPLOAD_SIZE_MB}MB limit")
    chunks.append(chunk)
file_bytes = b"".join(chunks)
```

### D5 — Tessdata download hardening (ISS-14 immediate)

**Problem.** `_try_download_tessdata` (`converters.py:755-768`) uses `urllib.request.urlretrieve`
with no timeout, no size limit, and no checksum:

```python
urllib.request.urlretrieve(url, dest)  # no hash, no timeout, no size limit
```

A missing timeout can hang the worker indefinitely. No size cap allows disk-fill. No
checksum verification means MITM on the GitHub download could inject malicious tessdata.

**Decision (immediate).** Replace `urlretrieve` with `urllib.request.urlopen(url, timeout=30)`
plus chunked read with a 100 MB cap. This prevents hangs and disk-fill. Checksum
verification is deferred to the Docker pre-bake phase (D5b) where the hash is pinned at
image build time.

```python
# Sketch:
import urllib.request
MAX_TESSDATA = 100 * 1024 * 1024  # 100 MB
req = urllib.request.urlopen(url, timeout=30)
total = 0
with open(dest, "wb") as f:
    while chunk := req.read(1_048_576):
        total += len(chunk)
        if total > MAX_TESSDATA:
            os.unlink(dest)
            raise RuntimeError(f"tessdata for '{lang}' exceeds 100MB cap")
        f.write(chunk)
```

### D5b — Pre-bake tessdata in Docker image (ISS-14 production)

**Decision.** Add `RUN curl -fsSL -o ...` lines to the Dockerfile for all expected
languages (`deu`, `eng`, `ara`). This removes the runtime download path entirely in
production. The `.tessdata/` directory with pre-baked data already exists per project
memory — this formalizes it as the production-only path. The runtime download (D5) remains
as a dev/local fallback.

### D6 — Remove MinIO fallback from `_list_docs_with_fallback` (ISS-05 long-term)

**Problem.** `_list_docs_with_fallback()` (`documents.py:39-80`) falls back to
`list_processed_docs()` (the O(N) MinIO listing) in four codepaths: backfill incomplete,
Postgres error, Redis error checking registry flag, and registry query returning `None`.
Every fallback triggers the full O(N) serial GET storm from ISS-05.

**Decision.** Once the registry is authoritative (ISS-03 from RFC-007 resolved, backfill
complete per RFC-006 D3), remove all MinIO fallback paths from `_list_docs_with_fallback`.
Registry `list_docs` is a single SQL query — there is no O(N) path left. If Postgres is
down, return an error, not a degraded O(N) listing.

**Dependencies.** ISS-03 (RFC-007) — registry dual-write correctness must be verified.
RFC-006 D3 — backfill must be complete. This is a breaking change for environments that
have not completed backfill.

## Implementation Plan

### Batch 0 — Immediate (no dependencies)

| Step | Issue  | Change                                                                                                                 | Files                  |
| ---- | ------ | ---------------------------------------------------------------------------------------------------------------------- | ---------------------- |
| 0.1  | ISS-21 | Remove`list_processed_docs()` from error paths in `get_document`, `get_document_structure`, `get_page_content` | `tools/documents.py` |

### Batch 1 — Short-term (no cross-RFC dependencies)

| Step | Issue  | Change                                                       | Files                              |
| ---- | ------ | ------------------------------------------------------------ | ---------------------------------- |
| 1.1  | ISS-15 | Chunked upload read +`MAX_UPLOAD_SIZE_MB` env var          | `upload_app.py`, `settings.py` |
| 1.2  | ISS-14 | Replace`urlretrieve` with `urlopen` + timeout + size cap | `converters.py`                  |

### Batch 2 — Pagination fix (depends on Batch 1 + ISS-07/RFC-008)

| Step | Issue   | Change                                                                                                                         | Files                                          |
| ---- | ------- | ------------------------------------------------------------------------------------------------------------------------------ | ---------------------------------------------- |
| 2.1  | ISS-05A | Compute and store`node_count` in `.meta.json` sidecar at save time; add `node_count` column to registry                  | `storage.py`, `registry.py`, migration SQL |
| 2.2  | ISS-06  | Pass`limit`/`offset` to `list_docs()` on registry path; read `node_count` from metadata instead of deserializing trees | `tools/documents.py`                         |

### Batch 3 — Docker pre-bake (ops change)

| Step | Issue  | Change                                                                              | Files          |
| ---- | ------ | ----------------------------------------------------------------------------------- | -------------- |
| 3.1  | ISS-14 | Pre-bake`deu.traineddata`, `eng.traineddata`, `ara.traineddata` in Dockerfile | `Dockerfile` |

### Batch 4 — Registry-only listing (depends on RFC-007 ISS-03 + RFC-006 D3 backfill)

| Step | Issue      | Change                                                                                   | Files                  |
| ---- | ---------- | ---------------------------------------------------------------------------------------- | ---------------------- |
| 4.1  | ISS-05B/D6 | Remove MinIO fallback from`_list_docs_with_fallback`; return error on Postgres failure | `tools/documents.py` |

## Test Strategy

### ISS-21 (D1) — Error path regression

- Unit test: call `get_document("nonexistent-id")` and assert response is
  `{"error": "Document not found: nonexistent-id"}` with no `available` key.
- Verify `list_processed_docs` is NOT called (mock it and assert zero calls).
- Repeat for `get_document_structure` and `get_page_content`.

### ISS-05A (D2) — Sidecar enrichment

- Unit test: call `save_doc_meta()` with a tree structure, read back `.meta.json`,
  assert `node_count` field is present and correct.
- Regression: existing `.meta.json` without `node_count` must not break
  `list_processed_docs` (field defaults to `None`/`0`).

### ISS-06 (D3) — Server-side pagination

- Unit test: mock `list_docs` and call `recent_documents(page=2, page_size=5)`. Assert
  `list_docs` was called with `limit=5, offset=5`, NOT `limit=100_000`.
- Assert `get_doc` is NOT called for node count enrichment (reads from metadata).
- Integration test with registry: insert 20 docs, request page 2 size 5, verify exactly
  5 results with correct offset.

### ISS-15 (D4) — Upload size limit

- Unit test: POST a file exceeding `MAX_UPLOAD_SIZE_MB`, assert HTTP 413 response.
- Unit test: POST a file under the limit, assert HTTP 200 (existing behavior preserved).
- Edge case: file exactly at limit boundary (100 MB) should succeed; 100 MB + 1 byte
  should fail.

### ISS-14 (D5) — Tessdata hardening

- Unit test: mock `urlopen` to return data exceeding 100 MB cap, assert
  `_try_download_tessdata` raises/returns False and partial file is cleaned up.
- Unit test: mock `urlopen` to hang (never return), assert timeout fires within 30s.
- Unit test: mock `urlopen` to return valid data under cap, assert file is written.

### ISS-21 + ISS-05 combined — DoS resistance

- Load test (manual, not CI): send 100 concurrent requests with invalid doc_ids.
  Measure: pre-fix = 100 * N MinIO GETs; post-fix (D1) = 0 MinIO GETs.

## Risks

1. **D1 breaks clients that parse the `available` array.** The `available` field in error
   responses is undocumented and the MCP tool description already directs clients to
   `recent_documents()`. Risk is low; mitigated by checking whether any known client
   parses this field (none identified in the codebase).
2. **D2 sidecar format change.** Adding `node_count` to `.meta.json` is additive (not
   breaking). Existing sidecars without `node_count` must be handled gracefully — the
   enrichment loop in `recent_documents` should fall back to `0` or `None` when the field
   is absent. The one-time backfill (RFC-006 D3) will re-generate sidecars.
3. **D3 registry pagination changes total-count behavior.** Current code uses
   `len(docs)` to set `DOCUMENTS_TOTAL` gauge (`documents.py:102`). With server-side
   pagination, `len(docs)` is `page_size`, not the corpus count. Must add a `count()`
   query or use the registry's existing count mechanism to preserve the gauge accuracy.
4. **D4 `MAX_UPLOAD_SIZE_MB` default may be too low for some corpora.** 100 MB covers
   all documents in the current 62-file validation corpus (largest is ~2 MB). If a user
   has legitimately large documents they can raise the env var. The default is
   conservative-safe.
5. **D5 timeout of 30s may be too short on slow networks.** The tessdata files for `deu`
   and `eng` are ~4 MB each; 30s is generous for a 4 MB download. The `ara` fast
   traineddata is ~2 MB. If a network is slower than ~130 KB/s this will fail — but the
   fallback behavior (log warning, return False) is unchanged. D5b (Docker pre-bake)
   eliminates the runtime download in production entirely.
6. **D6 is a breaking change for environments without backfill.** Removing the MinIO
   fallback means environments that have not completed the RFC-006 D3 backfill will get
   errors instead of degraded-but-working listings. This is intentional — the fallback
   is the performance problem. Sequencing: D6 ships only after backfill is confirmed
   complete, gated by the `pageindex:registry:complete` Redis flag.
