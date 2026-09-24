<!-- Space: CITRA -->
<!-- Title: Corpus Re-ingestion Audit — Run 22 (RFC-049 Contract Drift Remediation) -->
<!-- Folder: Audits -->
---
aliases:
  - Run 22
  - RFC-049 Corpus Baseline
tags:
  - audit
  - corpus
  - rfc-049
  - quarantine
---

# Corpus Re-ingestion Audit — Run 22 (RFC-049 Contract Drift Remediation)

## Environment

- Branch: `ICR-97-rfc49-contract-drift-remediation`
- Date: 2026-09-23
- Run 22 ID: `ba7ef8ac-1a4b-4497-84ee-84d1e21e0541` (single run_id across all 25 docs)
- Prior run: [[CORPUS_REINGESTION_AUDIT_RUN-21_RFC048|Run 21b]] (`d428364f-aa92-4a79-b409-e8bde5c31e6d`)
- Methodology: full clean-slate reset, then serial ingest via `preprocess_client.py` (`PREPROCESS_CONCURRENCY=1`)
- Profile: `PROFILE=local`; `VERDICT_DOWNGRADE_ENABLED=true`

**Baseline choice.** The RFC-049 tasks file names `rfc047-d9-final-baseline` as the comparison point. That is the wrong baseline for this run: RFC-048 is already in `HEAD`, and it moved Doc 12 from REJECTED to MARGINAL. Run 21b is the same-lineage predecessor and is used as the primary baseline here; the D9 column is retained for continuity.

### Clean-slate reset (RFC-046 injection order)

Executed before ingest, in `_ERASURE_MANIFEST` order (HR2):

| Step | Store | Action | Verified |
|------|-------|--------|----------|
| 0 | Backup | Diffed `uploads/` basenames vs `doc_store/` under NFC normalisation | All 94 objects matched a local file by name **and** byte size — no unique data destroyed |
| 1 | MinIO | Deleted all objects (290 inventoried first → `audit/ingest-runs/run22/preingest-inventory.json`) | 0 objects |
| 2 | Postgres | `doc_registry` TRUNCATE (rows dumped first) | 0 rows (backup was 3 bytes — registry already empty) |
| 3 | Redis | `DEL pageindex:hashes` + all `pageindex:registry:*` | db1 DBSIZE 0, `arq:*` untouched |
| 4 | Hash cache | Redis `pageindex:hashes` + legacy MinIO `hashes/processed_hashes.json` | Both absent |

The hash-cache clear is a precondition, not hygiene: without it `preprocess_client.py` short-circuits every unchanged document and the run is a silent no-op.

---

## Purpose

Validate [[049-contract-drift-remediation|RFC-049]] Wave 3:

1. **Task 8.1** — re-ingest the 25-doc corpus on the RFC-049 branch and score every document.
2. **Task 8.2** — diff against Run 21b; report any newly REJECTED document as a regression.

---

## Summary Scorecard

| # | Document | D9 Baseline | Run 21b | Run 22 | Delta |
|---|----------|-------------|---------|--------|-------|
| 1 | FEDERAL LAW NO (3) | MARGINAL | MARGINAL | MARGINAL | — |
| 2 | Federal Decree-Law 47 | PASS | PASS | PASS | — |
| 3 | GHV-TKV-Tarif | MARGINAL | MARGINAL | MARGINAL | — |
| 4 | Haftpflicht-Allgemeine | PASS | PASS | PASS | — |
| 5 | Haftpflicht-Besondere | PASS | PASS | PASS | — |
| 6 | Ministerial Resolution 279 | PASS | PASS | PASS | — |
| 7 | Reitlehrer | PASS | PASS | PASS | — |
| 8 | Unfallversicherung | MARGINAL | MARGINAL | MARGINAL | — |
| 9 | cabinet_resolution 21 | PASS | PASS | PASS | — |
| 10 | cabinet_resolution 96 | PASS | PASS | PASS | — |
| 11 | federal_decree_law 33 | PASS | PASS | PASS | — |
| 12 | **image pie chart (.jpg)** | REJECTED | MARGINAL | **MARGINAL** | — (holds) |
| 13 | uae_numbers landscape | MARGINAL | MARGINAL | MARGINAL | — |
| 14 | uae_numbers portrait | PASS | PASS | PASS | — |
| 15 | القرار التنظيمي | PASS | PASS | PASS | — |
| 16 | سياسة حوكمة | PASS | PASS | PASS | — |
| 17 | قرار مجلس الوزراء (1) | PASS | PASS | PASS | — |
| 18 | قرار مجلس الوزراء (106) | PASS | PASS | PASS | — |
| 19 | مرسوم اتحادي (13) | PASS | PASS | PASS | — |
| 20 | مرسوم اتحادي (33) | MARGINAL | MARGINAL | MARGINAL | — |
| 21 | وارد رقم 597 | MARGINAL | MARGINAL | MARGINAL | — |
| 22 | ﺣﻘﻮق اﻹﻧﺴﺎن | PASS | PASS | PASS | — |
| 23 | MOU MOHRE | PASS | PASS | PASS | — |
| 24 | اتفاقية مستوى الخدمة | PASS | PASS | PASS | — |
| 25 | world-stats-pocketbook | PASS | PASS | PASS | — |

**Run 22 Tally (25 docs):**

| Verdict | Run 21b | Run 22 | Delta |
|---------|---------|--------|-------|
| PASS | 18 | 18 | 0 |
| MARGINAL | 7 | 7 | 0 |
| FAIL | 0 | 0 | 0 |
| REJECTED | 0 | 0 | 0 |
| ERROR | 0 | 0 | 0 |
| **Total** | **25** | **25** | |

**0 regressions, 0 improvements — a doc-for-doc match to Run 21b.** RFC-049's D2-C reject override introduced no verdict change anywhere in the corpus, which is the intended result: the override is guarded on `first_defect ∈ {GARBLING, NODE_GARBLING}` after recovery, and no corpus document reaches that state post-RFC-048.

### MARGINAL residue (7) — reasons re-pulled live

| # | Document | Reason (Run 22) | Nodes |
|---|----------|-----------------|-------|
| 1 | FEDERAL LAW NO (3) | `depth_inadequate:expected_min_depth=4,actual_depth=2` | 595 |
| 3 | GHV-TKV-Tarif | `leaf_concentration=0.45` | 10 |
| 8 | Unfallversicherung | `leaf_concentration=0.31` | 9 |
| 12 | image pie chart | `leaf_concentration=0.71` | 3 |
| 13 | uae_numbers landscape | `depth_inadequate:expected_min_depth=2,actual_depth=1` | 0 (flat) |
| 20 | مرسوم اتحادي (33) | `depth_inadequate:expected_min_depth=4,actual_depth=3` | 555 |
| 21 | وارد رقم 597 | `depth_inadequate:expected_min_depth=3,actual_depth=1` | 0 (flat) |

Identical set and identical reason classes to Run 21b.

---

## Finding 1: Doc 12 is rescued by VLM alone — Surya contributes nothing

Doc 12 recovers exactly as in Run 21b, but the run-22 decision events make the attribution unambiguous:

```
surya_image_fallback  → choice=gate_not_triggered  reason="tesseract output acceptable"
    tesseract_chars=303  tesseract_garbled=false  surya_chars=0  winner=tesseract

post_validation_image_fallback → choice=vlm
    trigger_defects=["depth<2","garbling","node_garbling"]
    vlm_chars=616   vlm_garbled=false
    surya_chars=0   surya_garbled=true   surya_confidence=0.0
    winner=vlm
```

**Surya returned 0 characters and was itself garbled.** The entire recovery is the VLM leg (`indexer.py:1614`), reached through the RFC-048 post-`validate_tree` parallel window. The pre-VT Surya gate never fired because tesseract's 303 chars cleared `MIN_STANDALONE_IMAGE_MD_CHARS=100` and the gate-level garble check returned false — the Run 21a sensitivity gap, unchanged and still latent.

**Consequence for the negative control.** Disabling `SURYA_FALLBACK_ENABLED` alone would not change this document's outcome by a single character, because Surya already contributes zero. A control built on that lever would appear to run and would prove nothing. `VLM_FALLBACK` (`config.py:325`) is the only env var that gates the post-VT VLM leg, and it must be disabled for the control to be meaningful. This was established from Run 21b and is now independently reconfirmed by Run 22's own telemetry.

Minor drift from Run 21b: VLM returned 616 chars here vs 643 there, moving `leaf_concentration` 0.72 → 0.71. Same verdict; ordinary VLM sampling variance.

---

## Finding 2: Zero quarantine writes — D2-C remains unexercised on real data

`quarantine/` is **empty** in the live bucket after the full run, and the run log contains no `Rejecting low-quality`, no `LowQualityTreeError`, and no `quarantine write failed`. All 24 `flat_garble_unrecovered_reject` decision events resolved to `proceed_to_route_dispatch` ("no flat garble reject").

This is the expected outcome, not a defect: RFC-048 recovers the only document that previously reached a garbling rejection. But it has a direct evidentiary consequence —

> **The corpus run alone provides no evidence that the RFC-049 D2-C reject + quarantine path works on a real document.** It demonstrates only that the path does not fire spuriously (a genuine no-regression result).

Contract OCR-01-C4 is covered at unit level by `tests/test_quarantine.py` (13 passed), which was tightened in this wave to assert the actual `save_quarantine(sha256, tree, [filename])` call. End-to-end evidence requires the negative control below.

---

## Finding 3: FLAT-routed documents write a sidecar without `doc_name`

Three documents (13, 14, 21 — the FLAT-routed set, `node_count=0`) have `processed/<doc_id>.meta.json` with `doc_name: ""`, `source_url: ""` and `processed_at: ""`, carrying only verdict fields (`sidecar_version: 4`).

This is not corruption — the sidecar declares `consistency_regime: postgres-authoritative`, and `doc_registry` holds the correct `doc_name` for all three. Any tooling that resolves document identity from the MinIO sidecar alone will mis-attribute FLAT-routed documents. Scoring for this audit was therefore joined against the registry, not read from the sidecar.

Filed as a P3 observation; no RFC-049 action.

---

## Finding 4: Negative control — D2-C reject + quarantine verified end-to-end

Finding 2 leaves the D2-C path unexercised on real data. A negative control was run to close that gap: force Doc 12 back into the unrecovered-garbling state that RFC-048 rescues it from, and observe whether the RFC-049 override fires.

### Lever selection (source-verified, not assumed)

Two env vars, and only two, gate the recovery legs that reach Doc 12:

| Lever | Source | Effect when `false` |
|-------|--------|---------------------|
| `VLM_FALLBACK` | `config.py:325` | Closes the post-`validate_tree` VLM leg — the leg that actually rescues Doc 12 (Finding 1) |
| `SURYA_FALLBACK_ENABLED` | `config.py:349` | Closes the Surya leg — contributes 0 chars here, disabled for completeness so the control has no open recovery path |

`FLAT_DOC_ROUTING` was considered and **rejected as a lever.** Doc 12's `trigger_defects` list `depth<2` first, which suggests `first_defect=DEPTH_LOW` and therefore a FLAT route. That reading is wrong: the D4 rule in `tree_validation.py` (~536-546) promotes a co-firing garble defect to primary "so OCR recovery dispatches correctly", and `decide_route()` maps `GARBLING → retry_ocr → Route.TREE` independently of `FLAT_DOC_ROUTING`. Changing it would have introduced a confounding variable without affecting the outcome. The run below confirms this from telemetry.

### Procedure

1. Full HR2 erasure of Doc 12 (`cc32906f-6996-4b63-81ce-3a41470e93e2`, sha8 `19aad2bc`) across all four stores; each verified clean.
2. Re-ingest `image pie chart about labor distribution in january 2025 - Copy.jpg` with `VLM_FALLBACK=false SURYA_FALLBACK_ENABLED=false PREPROCESS_CONCURRENCY=1`.
3. Capture decision events and MinIO prefix counts during the rejection.
4. Restore: erase again, re-ingest under normal env, re-verify the published corpus state.

Exported env vars are authoritative here — `preprocess_client.py:124` calls `load_dotenv()` with the default `override=False`, so no `.env` edit was needed and none was made.

### Result — the override fires

| Decision event | Observed |
|----------------|----------|
| `post_validation_image_fallback` | `choice=none  vlm_chars=0  surya_chars=0  winner=none` — both legs closed; **did not fail open** |
| `route_selected` (computed) | `tree, forced=false, first_defect=garbling` — D4 promotion empirically confirmed |
| `route_selected` (override) | `choice=reject  reason=forced_route  forced=true  computed_route=tree  final_route=reject  first_defect=garbling` ← **D2-C** |
| `flat_garble_unrecovered_reject` | `proceed_to_route_dispatch` — the flat clause did **not** fire |
| Outcome | `Rejecting low-quality tree … reason=garbling` → `LowQualityTreeError` |

The last two rows matter: they establish that rejection came from the **tree-route D2-C override in `index()`**, not from the pre-existing flat-route quarantine clause in `_persist_flat_result`. Contract OCR-01-C4 is exercised on the intended code path.

### Quarantine artifacts

| Object | Size | Content |
|--------|------|---------|
| `quarantine/19aad2bc…4163.json` | 3102 B | tree dict |
| `quarantine/19aad2bc…4163.meta.json` | 98 B | `{"filenames": ["image pie chart about labor distribution in january 2025 - Copy.jpg"]}` |

**HR5 upheld.** During the rejection `processed/` stayed at 48 and `uploads/` at 24 — the rejected tree never reached the served surface. It exists only as the unserved `quarantine/` diagnostic copy the hard rule permits.

### Restoration

Re-ingested under normal env: `processed` 50, `uploads` 25, registry 25 rows, 18 PASS / 7 MARGINAL, Doc 12 MARGINAL at 3 nodes. `quarantine/` returned to **0 objects**, which incidentally confirms `clear_quarantine(sha256)` fires on a later successful ingest of the same hash. The corpus is back in its published Run-22 state.

### Scope limit

This proves the D2-C path works end-to-end for one document under forced conditions. It does **not** show that any naturally-occurring corpus document reaches D2-C — Finding 2's result stands: under production config, zero do.

---

## Verification Protocol

Per the standing rule that no audit may reuse cached MinIO values, every figure in this document was re-pulled live after the run completed:

- 25/25 `processed/<doc_id>.meta.json` fetched fresh from MinIO
- 25/25 cross-checked against `doc_registry` (Postgres) — **0 verdict mismatches**
- 25/25 tree artifacts confirmed present: 22 `processed/<doc_id>.json`, 3 `processed/<doc_id>.flat.json`, **0 missing**
- Live bucket prefix counts: `processed` 50, `uploads` 25, `figures` 1, `quarantine` **0**
- No stray `converters_cli` child processes after the run (`pgrep` clean)

### Test-suite hygiene

`tests/test_quarantine.py` previously ran the real `save_quarantine()` and wrote 8 objects into the production bucket. Both probe helpers now mock it. Re-verified by running the file with `MINIO_BUCKET` pointed at a throwaway bucket:

- `13 passed`
- throwaway bucket: **0 objects** — the suite no longer writes to MinIO at all
- live bucket unchanged

> **Host note.** Neither `make test` nor `make test-uncapped` runs on this Darwin host: `make test` requires `systemd-run` (Linux-only) and `make test-uncapped` calls `timeout`, which is absent on macOS without coreutils. The run above was bounded with an explicit `perl -e 'alarm'` SIGALRM wrapper, foreground, on a single narrowly-selected file. The CLAUDE.md prohibition on unbounded/backgrounded runs was honoured. This gap is filed as an action item.

---

## Action Items

| Priority | Item | Status |
|----------|------|--------|
| **P0** | Negative control: re-ingest Doc 12 with **both** `VLM_FALLBACK=false` and `SURYA_FALLBACK_ENABLED=false` to exercise D2-C reject + quarantine on a real document (Finding 2) | **Done** — Finding 4; `forced_route` override observed, corpus restored |
| P1 | Verify `quarantine/<sha256>.json` + `.meta.json` land, and confirm the control did not fail open | **Done** — Finding 4; both objects written, `winner=none` (no fail-open) |
| P1 | arq-path check — ingest a rejected doc via `POST /upload/files` and verify `sha256` in `GET /upload/status/{job_id}` (Task 7.5d) | Open |
| P2 | `make test` / `make test-uncapped` are both unusable on macOS — add a portable timeout or document the platform gap | Open |
| P3 | FLAT-routed sidecars omit `doc_name` (Finding 3) — document that the registry is authoritative | Open |
| P3 | Pre-VT Surya image gate sensitivity gap persists (Finding 1) — latent, masked by the post-VT window | Open |

---

## Traceability

| Artifact | Reference | Path |
|----------|-----------|------|
| RFC | [[049-contract-drift-remediation\|RFC-049]] | `agents/rfcs/049-contract-drift-remediation.md` |
| Prior run | [[CORPUS_REINGESTION_AUDIT_RUN-21_RFC048\|Run 21]] | `audit/CORPUS_REINGESTION_AUDIT_RUN-21_RFC048.md` |
| Upstream RFC | [[048-surya-image-fallback\|RFC-048]] | `agents/rfcs/048-surya-image-fallback.md` |
| Reset procedure | [[046-ocr-attribution-failure-cluster-remediation\|RFC-046]] | clean-slate injection order |
| D2-C implementation | `index()` REJECT arm | `src/pageindex_mcp/client/indexer.py` (`case (False, Route.REJECT)`) |
| Flat quarantine clause | `_persist_flat_result` | `src/pageindex_mcp/client/indexer.py` |
| Contract tests | OCR-01-C4 probes | `tests/test_quarantine.py` (commit `3217165`) |
| Run log | Run 22 ingest | `.run/run22-ingest.log` (3021 lines, exit 0) |
| Pre-wipe inventory | 290 objects | `audit/ingest-runs/run22/preingest-inventory.json` |
