# Implementation Blueprint — Couple 3: Fatten `.meta.json` sidecar + incremental O(Δ) registry reconcile

**Audit source:** `audit/IMAGE_BLOCK_INGESTION_SCALING_AUDIT_2026-07-21.md` — Finding 9 (`registry_backfill.py:199` `_bounded_enrich` → `read_registry_fields` whole-tree GET per sidecar every tick) and P0 remediation (C-3 fatten sidecar + incremental reconcile).
**Branch:** `feat/image-block-picture-ocr`. **Owned files:** `src/pageindex_mcp/storage.py`, `src/pageindex_mcp/registry_backfill.py`, `src/pageindex_mcp/client.py` (meta-dict payload only), plus tests. **Design-only — builder implements.**

## 0. Constraints (quoted, must hold)

- **HR2 erasure cascade:** "any new/fattened sidecar object must be covered by `delete_doc`'s purge (verify the purge already lists `processed/*.meta.json`; if the sidecar path changes, purge coverage must move with it)." → The fattened sidecar keeps the **same object path** `processed/<doc_id>.meta.json` (only fields grow), so `delete_doc` step 3 (`storage.py:242-248`) already covers it — **no path change**. The **new** derived store (Redis reconcile-etag map) **must join the cascade**.
- **Backward compatibility:** "Reconcile must handle: fat sidecar (fast path), thin sidecar (fetch full JSON once, then WRITE the fattened sidecar so the next tick is O(Δ) — self-healing), no sidecar (same legacy fallback)."
- **Do not rewrite `registry_backfill.py`:** "The registry enrichment work just landed in commits 0c0bcf2/47c10f3 — read `registry_backfill.py` as-is and extend, don't rewrite." Static gate has a **pre-existing C901 on `_upsert_all` (`registry_backfill.py:131`)** and **ruff-format drift in storage.py** — the builder MAY clean these while editing (the conditional-enrich change naturally reduces `_upsert_all` complexity; extract helpers).
- **No new Prometheus metric names** without an existing fit; **metrics.py is read-only.** Confirmed: **no existing metric fits** "sidecars fetched / full-JSON fallbacks / Δ docs", and we cannot add one. → Observability is via a **structured log line** + the existing `_record_reconcile_heartbeat` only. No metric.
- **pgvector / C-6 EXCLUDED** (needs an ADR — out of scope).
- **HR1:** never claim vectorless beats vector RAG in any docstring.
- **client.py image-path logic was just rebuilt by another couple — must not be restructured.** Only the meta-dict payload (2 lines) changes.

## 1. Sidecar schema (fields, new marked, writer sites, versioning/compat)

The sidecar at `processed/<doc_id>.meta.json` written by `save_doc_meta` (`storage.py:333`) gains the two registry-critical fields that today only live in the full processed JSON (`_REGISTRY_FIELDS`, `storage.py:401-412`), plus a version marker.

**Fattened sidecar (v2) fields:**

| field | status | source | write rule |
|---|---|---|---|
| `doc_id`, `doc_name`, `source_url`, `processed_at` | existing | base | always (defaulted `""`) |
| `content_class` | existing | flat only | when present |
| `node_count` | existing | derived/explicit | when derivable |
| `verdict`, `verdict_reason`, `max_leaf_ratio`, `pipeline_version`, `permanent_marginal`, `promotion_eligible`, `verdict_computed_at` | existing | verdict calc | omit-when-absent |
| **`sha256`** | **NEW** | ingest file hash | **omit-when-absent** |
| **`doc_description`** | **NEW** | LLM/fork description | **write when key present** (empty string is valid; use `"doc_description" in meta`, not truthiness) |
| **`product`, `tier`, `doc_family`, `effective_date`** | **NEW (forward-compat)** | C-1 facets (P2, not yet generated) | omit-when-absent — keeps the fat-path lossless once C-1 lands, no-op today |
| **`sidecar_version`** | **NEW** | constant | **always `= 2`** |

**Versioning/compat story:** `sidecar_version` is the explicit generation marker (thin/legacy sidecars have no such key → treated as v1). The **fat-vs-thin decision uses field presence, not the version int**, so it is robust to version drift: `_is_fat(meta) = "sha256" in meta and "doc_description" in meta`. No existing sidecar carries `sha256` today (confirmed: `save_doc_meta` currently persists only base+content_class+node_count+verdict), so the signal is clean. `sidecar_version` is documentation/telemetry.

**Writer call sites (every one):**

1. **`storage.py:save_doc_meta` (333-395)** — the single writer. Extend the persisted set per the table above. This alone fattens **flat docs** (finding: `save_flat_doc` at `storage.py:135-158` calls `save_doc_meta(doc_id, data)` with the **full flat dict**, which already carries `sha256` and `doc_description` — client.py flat block `831-845`). No flat-path client change needed.
2. **`client.py` tree-doc meta dict (911-921)** — this dict does **not** carry `sha256`/`doc_description` today. Add exactly two lines so the tree sidecar is fat at ingest:
   ```python
   "sha256": sha256,                                   # NEW
   "doc_description": result.get("doc_description", ""),  # NEW
   ```
   `sha256` is in scope (computed `client.py:381`); `result` is the fork output (its `doc_description` is written to full JSON at `client.py:903`). This is a pure payload addition — no image-path restructuring.
3. **`registry_backfill.py` self-heal rewrite** (new call, see §2) — reuses `save_doc_meta` to upgrade a thin sidecar to v2 in place.

## 2. Incremental reconcile algorithm (step by step)

**Comparison-key decision (be precise):** The MinIO `list_objects(..., recursive=True)` response carries per-object **`etag`** and **`last_modified`** for free (no GET) — confirmed available on the SDK object, currently read nowhere. **`etag` is THE "changed" decider.** It is the sidecar object's content hash (single-part PUT → MD5, stable across identical writes; changes iff the sidecar is rewritten, i.e. the doc was re-ingested). We compare the **listing etag against a Redis-stored last-seen etag per doc_id**.

**Why NOT the audit's literal "registry `synced_at`/stored hash":** confirmed there is **no `synced_at` column** in `doc_registry`, **no migration** adds one, `upsert_doc` writes no timestamp, and **no bulk reader returns `{doc_id: sha256}` or `{doc_id: synced_at}`** (`list_docs` selects no `sha256`). Building the audit's literal design needs registry.py edits (migration + bulk reader) — **out of this couple's owned files**. The `sha256` inside the sidecar is not visible at listing time and the registry can't be bulk-queried for it cheaply, so it **cannot** be the listing-time comparator. Decision: **Redis etag map** (`pageindex:registry:reconcile_etags`, HSET `doc_id → etag`), co-located with the existing reconcile state (`_REGISTRY_LAST_RECONCILE_AT_KEY`, `registry_backfill.py:65`) and mirroring the `hash_cache_*` Redis pattern. `sha256` is what we **persist to the registry**, not what we compare. **Open question flagged in §7 (Path B).**

**Algorithm (new `reconcile_registry_drift` body, replacing `registry_backfill.py:410-437`):**

1. Guards unchanged (`registry_enabled`+`postgres_dsn` `393`, pool ready `396-400`, redis connect `402-408`).
2. `entries = await to_thread(_list_meta_entries)` — list of `(object_key, etag, doc_id)` for every `processed/*.meta.json`. **Cheap: listing metadata only, no per-doc GET.** (Optionally also return `orphan_doc_ids` — doc_ids with `processed/<id>.json`/`.flat.json` but **no** `.meta.json`; see §2b.)
3. If `not entries` → heartbeat + return (unchanged, `416-419`).
4. `stored = await to_thread(reconcile_etag_get_all)` → `{doc_id: etag}` from Redis HGETALL (bytes-normalized).
5. `full_minio_doc_ids = {doc_id for _, _, doc_id in entries}` — the full current MinIO doc set for deletion detection (built from the **listing**, so it stays complete even though we GET only Δ).
6. `changed = [(k, etag, doc_id) for (k, etag, doc_id) in entries if stored.get(doc_id) != etag]` — new docs (absent from `stored`) and re-ingested docs (etag differs). **This is Δ.**
7. If `changed`:
   - `failed = await _upsert_all([k for k,_,_ in changed], dry_run=False)` — inside `_upsert_all`, each Δ sidecar takes the **fat path (no full-JSON GET)** or **thin self-heal** (§2a).
   - `to_store = {doc_id: etag for (k, etag, doc_id) in changed if k not in set(failed)}` — persist etags **only for successful upserts** (a failed doc keeps its old/missing etag so it retries next tick).
   - `await to_thread(reconcile_etag_set_many, to_store)`.
   - Log: `"reconcile: %d listed, %d changed, %d upsert-failed, %d full-json-fallbacks"` (fallback count returned/counted from `_upsert_all` — see §2a).
   - Else log `"reconcile: %d listed, 0 changed"`.
8. `await _delete_stale_rows(full_minio_doc_ids)` — **deletion detection unchanged** (`list_all_doc_ids()` − `full_minio_doc_ids`, with the `_MAX_STALE_DELETE_FRACTION=0.5` safety guard, `457-495`). Extend it to also **prune etag entries** for deleted docs (`reconcile_etag_delete` per stale doc, or `reconcile_etag_prune(full_minio_doc_ids)` after).
9. Heartbeat unchanged (`_record_reconcile_heartbeat`, `437`).

**Complexity:** listing O(N) metadata (unchanged, cheap) + O(Δ) small-sidecar GETs (was O(N) full-tree GETs). Full-JSON GET happens **only** for thin/orphan docs, once each, then never again (self-healed).

**§2a — `_upsert_all` conditional enrich + self-heal** (rewrite `_bounded_enrich`, `195-202`):
```
_is_fat(meta) = "sha256" in meta and "doc_description" in meta

async def _bounded_enrich(key, meta):
    async with enrich_sem:
        if _is_fat(meta):
            return key, meta, False          # fast path — NO read_registry_fields
        doc_id = meta.get("doc_id", "")
        rich = await to_thread(read_registry_fields, doc_id, meta.get("content_class"))
        if rich:
            meta.update(rich)                 # now has sha256, doc_description, facets
            await to_thread(save_doc_meta, doc_id, meta)   # SELF-HEAL: rewrite as v2 fat sidecar
            return key, meta, True            # did a full-json fallback
        return key, meta, False
```
Return the fallback flag so reconcile can count/log it. `_upsert_all`'s public signature and return type (`list[str]` of failed keys) stay unchanged — only internals change (also trims the C901). The self-heal rewrite changes the sidecar's etag; because we store the **pre-rewrite listing etag** in step 7, a healed doc re-verifies on the next tick and then takes the **fat path with no full GET** (2-tick convergence, one extra small-sidecar GET). Acceptable; §7 notes an exact-once refinement.

**§2b — no-sidecar (orphan) legacy self-heal (optional, separable):** to fully satisfy the "no sidecar (same legacy fallback)" requirement, `_list_meta_entries` also returns `orphan_doc_ids` (a `processed/<id>.json` or `.flat.json` with no `.meta.json` — mirror `list_processed_docs`'s meta-preference logic, `storage.py:470-484`). Reconcile treats each orphan as thin: `read_registry_fields` once → `save_doc_meta` writes a fresh fat sidecar → subsequent ticks are O(Δ). This is a bounded one-time cost. **Mark clearly:** if the builder must split P0, ship §2b as an immediate follow-up — current reconcile (and `_list_meta_keys`) already ignores no-sidecar docs, so omitting §2b is not a regression.

## 3. Files & symbols to change (on-disk line anchors)

**`storage.py`**
- `save_doc_meta` (333-395): add `sha256`/`doc_description` (+ forward-compat facets) omit-when-present, and `sidecar_version=2` always. Add these to the `_META_FIELDS` doc tuple (318-330) for consistency.
- New Redis reconcile-etag helpers, placed beside `hash_cache_*` (623-651), using the same `get_cache_redis()` sync pattern (called via `to_thread` from async reconcile): `RECONCILE_ETAG_KEY = "pageindex:registry:reconcile_etags"`, `reconcile_etag_get_all`, `reconcile_etag_set_many`, `reconcile_etag_delete`, `reconcile_etag_prune`.
- `delete_doc` (162-315): add **step 4b** — `reconcile_etag_delete(doc_id)` right after the Redis cache invalidation (step 4, 250-257); update the cascade docstring (163-169) to list it. New derived store joins HR2. `read_registry_fields` (415-460) — **no change** (it stays the thin-sidecar full-JSON fallback).

**`registry_backfill.py`**
- New `_list_meta_entries() -> list[tuple[str,str,str]]` (+ orphan set if §2b) beside `_list_meta_keys` (80-88); keep `_list_meta_keys` for `main()`/`run_auto_backfill` unchanged.
- New `_is_fat(meta)` helper.
- `_upsert_all` (131-235) — `_bounded_enrich` (195-202) conditional enrich + self-heal per §2a.
- `reconcile_registry_drift` (376-437) — body rewritten to §2 (guards/heartbeat preserved).
- `_delete_stale_rows` (460-495) — add etag prune for deleted docs.

**`client.py`**
- Tree-doc meta dict (911-921): +2 lines (`sha256`, `doc_description`). Flat block (831-845): **no change**.

## 4. New / changed function signatures

```python
# storage.py — new
RECONCILE_ETAG_KEY: str = "pageindex:registry:reconcile_etags"
def reconcile_etag_get_all() -> dict[str, str]: ...
def reconcile_etag_set_many(mapping: dict[str, str]) -> None: ...
def reconcile_etag_delete(doc_id: str) -> None: ...
def reconcile_etag_prune(live_doc_ids: set[str]) -> None: ...
# storage.py — unchanged signatures, extended behavior
def save_doc_meta(doc_id: str, meta: dict) -> None: ...
async def delete_doc(doc_id: str) -> dict: ...   # + reconcile_etag_delete step

# registry_backfill.py — new
def _list_meta_entries() -> list[tuple[str, str, str]]: ...   # (object_key, etag, doc_id)
def _is_fat(meta: dict) -> bool: ...
# unchanged signatures, extended behavior
async def _upsert_all(meta_keys, dry_run, collect_doc_ids=None) -> list[str]: ...
async def reconcile_registry_drift() -> None: ...
async def _delete_stale_rows(minio_doc_ids: set[str]) -> None: ...
```
No public signature changes — protects `test_worker_coverage` (patches `reconcile_registry_drift`), `test_registry_backfill` (mocks `_upsert_all`), and the cron wrapper.

## 5. TDD build sequence (RED first)

Write each test failing, then implement. Suggested files: `tests/test_storage_meta.py` (sidecar), `tests/test_reconcile_incremental.py` (new), `tests/test_storage_contract.py` (HR2).

1. **RED** `test_save_doc_meta_persists_sha256_and_doc_description` — `save_doc_meta` with meta carrying `sha256`+`doc_description`; assert written JSON contains both and `sidecar_version == 2`. → implement §1(1).
2. **RED** `test_save_doc_meta_doc_description_empty_string_kept` — `doc_description=""` present → key persisted (presence, not truthiness).
3. **RED** `test_save_doc_meta_omits_sha256_when_absent` — meta without `sha256` → no `sha256` key (but `sidecar_version` present).
4. **RED** `test_client_tree_meta_carries_sha256_and_description` — assert the tree ingest path passes `sha256`+`doc_description` into `save_doc_meta` (patch `save_doc_meta`, assert call kwargs). → implement §1(2).
5. **RED** `test_reconcile_fat_sidecar_avoids_full_json_get` — `_list_meta_entries` returns fat sidecars, empty stored etags; **patch `read_registry_fields`, assert call_count == 0**; assert `upsert_doc` called. (fat-path via mock call counts — the finding-9 proof.)
6. **RED** `test_reconcile_thin_sidecar_self_heals` — thin sidecar (no `sha256`); patch `read_registry_fields`→rich dict, patch `save_doc_meta`; assert `read_registry_fields` called once **and** `save_doc_meta` called once with `sha256` in the rewritten meta.
7. **RED** `test_reconcile_no_sidecar_legacy_orphan_heal` (if §2b) — orphan `.json`, no `.meta.json` → `read_registry_fields` once + `save_doc_meta` writes fat sidecar.
8. **RED** `test_reconcile_incremental_skips_unchanged` — stored etag == listing etag → `read_registry_fields` and `upsert_doc` **not** called for that doc (O(Δ)=0).
9. **RED** `test_reconcile_changed_etag_reprocessed` — stored etag ≠ listing etag → doc upserted; new etag stored.
10. **RED** `test_reconcile_stores_etag_only_after_successful_upsert` — one doc fails upsert → its etag **excluded** from `reconcile_etag_set_many`; succeeded doc included.
11. **RED** `test_reconcile_deletion_detection` — registry has doc_id absent from MinIO listing → `_delete_stale_rows` deletes it **and** its etag pruned (respect `_MAX_STALE_DELETE_FRACTION`).
12. **RED** `test_delete_doc_purges_reconcile_etag` (HR2) — `delete_doc` calls `reconcile_etag_delete(doc_id)`; assert `processed/<id>.meta.json` still in remove_object calls (keeps `test_delete_doc_removes_meta_sidecar` green).

## 6. Existing tests that break + how to update

- **`tests/test_storage_meta.py::test_save_doc_meta_writes_sidecar` (line 19)** — asserts `json.loads(written) == meta` (4 base fields). BREAKS on new `sidecar_version`. **Update:** assert the 4 fields are a subset and `sidecar_version == 2`.
- **`tests/test_storage_meta.py::test_save_doc_meta_verdict_fields_absent_legacy_compat` (line 174, key-set assert at 187)** — asserts exact key set `{doc_id,doc_name,source_url,processed_at}`. BREAKS. **Update:** expected set becomes `{...4 base..., "sidecar_version"}`; `sha256`/`doc_description`/verdict fields still absent (not supplied). Keep it as the "thin-input still minimal" guard.
- **`tests/test_storage_contract.py::test_erase_01_c1_cascade_order_across_stores` (line 95)** — likely asserts cascade step order/coverage. Adding step 4b (`reconcile_etag_delete`) may shift it. **Update:** add the new Redis-etag purge to the expected cascade (right after step 4). Verify it asserts order/coverage, not an exhaustive equality that now over-fails.
- **`tests/test_rfc012_backfill_gather.py`** (`_upsert_all`, funcs at 28/54/86) — bounded-concurrency gather tests. If any feed **thin** metas and implicitly expect `read_registry_fields` to run, the conditional enrich changes behavior. **Verify/update:** ensure fixtures are explicitly fat or thin per the case; assert accordingly. No signature change, so pure-concurrency assertions stay green.
- **`tests/test_worker_coverage.py::test_reconcile_registry_drift_cron_delegates` (449)** — patches `reconcile_registry_drift`; **unaffected**.
- **`tests/test_registry_backfill.py`** — mocks `_upsert_all`; **unaffected**.
- Static gate: fixing the **C901 on `_upsert_all`** (extract `_is_fat`/`_bounded_enrich` helpers) and **ruff-format drift in storage.py** should clear those pre-existing gate failures in-passing.

## 7. Risks / open questions

1. **Path B (registry `synced_at` column) vs Path A (Redis etag map).** Blueprint chooses **Path A** because registry.py is out of scope, no `synced_at` column/migration/bulk-reader exists, and sha256 isn't listing-visible. Path B (add `synced_at` + a `{doc_id: synced_at}` bulk reader in registry.py) would make reconcile state survive a Redis flush and align with the audit's literal wording, but needs a scope expansion + migration (following the `_MIGRATE_NODE_COUNT_SQL` pattern) — **flag to swarm lead / reviewer.**
2. **Redis etag-map durability.** A Redis flush empties the map → the next tick does one full pass — but **sidecar-only GETs** (fat sidecars → no full-JSON GET), then O(Δ) resumes. Self-healing, bounded. Acceptable.
3. **Ingest-time dual-write vs etag map.** `worker._upsert_registry_row` (worker.py, not owned) writes the registry row at ingest but does **not** touch the etag map → the next reconcile tick sees the new sidecar etag ≠ stored and does **one redundant idempotent upsert**, then stores the etag. Harmless (upsert is `ON CONFLICT DO UPDATE`). Documented; no worker.py change.
4. **etag stability.** Sidecars are tiny → single-part PUT → etag == content MD5, change-stable. With server-side encryption the etag may not equal MD5 but is still change-stable (which is all we require). Strip surrounding quotes from `obj.etag`.
5. **Thin-heal 2-tick convergence** (one extra small-sidecar GET per healed doc). Optional exact-once refinement: have `save_doc_meta` return the `put_object` result's `.etag` and store it immediately — deferred to avoid changing `save_doc_meta`'s `-> None` contract and its tests.
6. **`decode_responses`.** `get_cache_redis`/`get_async_redis` may return bytes; normalize HGETALL keys/values to `str` in `reconcile_etag_get_all`.
7. **No reconcile metric** (metrics.py read-only, no fit). Δ visibility is log-only + the existing heartbeat. If a metric is later wanted, it needs a metrics.py change owned by another couple.
