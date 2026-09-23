<!-- Space: CITRA -->
<!-- Title: RFC-049: Contract Drift Remediation -->
<!-- Folder: RFCs -->

---
id: "RFC-049"
title: "Contract Drift Remediation"
type: rfc
status: accepted
date: "2026-09-23"
plan-impact: "no"
tags:
  - rfc
  - contracts
  - hard-rules
  - gates
  - erasure
aliases:
  - "RFC-049"
  - "Contract Drift Remediation"
governs:
  - "[[design-rfc049-contract-drift-remediation]]"
  - "[[tasks-rfc049-contract-drift-remediation]]"
supersedes: []
---

## Context

On 2026-09-23 the contracts gate (`scripts/gates/contracts.sh`) stood at **PASS=44 FAIL=22**. The gate is a grep: for every `id:` in `agents/contracts/*.yaml` it searches `tests/` for a literal occurrence of the contract ID. A FAIL therefore means only "no test mentions this ID" — it says nothing about whether the behaviour is covered.

**(Amendment 2026-09-23):** The PASS/FAIL counts are line counts, not contract counts. 5 of the 64 PASSes are module-coverage lines rather than contract-ID lines; the contract files define **61** contract IDs, of which **59** pass and 2 fail. The arithmetic below (PASS=65 FAIL=1 after items 1–5) is on the gate's own line-count basis and still holds.

The remediation triaged all 22. The expectation going in was that most were **label drift**: a test consolidation (see ~~[[test-suite-reduction-2026-09]]~~ git `91e7249` — *"test: consolidate the suite from 2510 collected to 923"* **(Amendment 2026-09-23: the wikilink resolved nowhere)**, 2510 → 923 tests) had dropped the ID comments while keeping the assertions. That expectation was wrong. The failures were overwhelmingly **missing tests** — the behaviour genuinely had no assertion anywhere — and in two cases something worse: **the contract had drifted away from the code**.

That second category is the reason this RFC exists, and it is worth stating plainly once:

> A contract that no longer matches the code is worse than a missing test. A missing test reports red and gets written. A drifted contract reports **green** while pointing at a test that proves the opposite of what the code does — and the gate, which is a grep, cannot tell the difference. The drift is invisible precisely because it is labelled.

Both drifted contracts were found the same way: an agent wrote a probe test *literally to the contract text*, and the probe failed against production code. That is the only reliable detector the current gate design admits.

The gate now stands at **PASS=64 FAIL=2**. The two remaining FAILs are held open **deliberately**: `LANG-01-C2` and `OCR-01-C3` are the two drifted contracts, and labelling their tests before the contract text is amended would convert a true red into a false green. They are D1 and D2 below.

```
$ bash scripts/gates/contracts.sh   # 2026-09-23, this branch
  [FAIL]  contracts[LANG-01-C2]: NOT found in tests/ — write a test containing 'LANG-01-C2'
  [FAIL]  contracts[OCR-01-C3]: NOT found in tests/ — write a test containing 'OCR-01-C3'
Gate 3 contracts: PASS=64  FAIL=2  WARN=0
```

### Relationship to Prior RFCs

- **[[RFC-047]]** (gate-layer correctness): D4 re-derived the flat-path defect set and D2 made the flat-blocks garble check character-mass-ratio-aware. Both are upstream of D2 here — the tree/flat asymmetry this RFC surfaces is the residue of RFC-047's flat-side work not having a tree-side counterpart.
- **[[RFC-044]]** (recovery dispatch wiring): owns `decide_route` and the `REASON_POLICY` authority model. D2 Option A would change one entry in that table; the table itself is RFC-044's.
- **[[RFC-043]]** (OCR / garble / erasure hardening): owns the erasure cascade that D5 patches.
- **[[RFC-030]]** (run-13 regression fixes): introduced the persist-with-FAIL-verdict position that D2 Option B would ratify.
- **[[RFC-041]]** (recurring-defect consolidation): D8/Property 8 built the lifecycle lint. D6 is the same class of defect one gate over — an unhardened grep in a CI gate.

## Goals

1. Bring `LANG-01-C2` and `CONV-01-C5` into agreement with a deliberate, documented, test-pinned behaviour in `ocr_langs.py`, so the two contracts stop asserting the opposite of the code. **(Amendment 2026-09-23):** the `CONV-01-C5` amendment is itemised in the Decision Summary as D4(c); it shares D1's form of words and is sequenced after D1.
2. Put the `OCR-01-C3` / Hard Rule 5 contradiction in front of a human with both resolutions costed, and take neither unilaterally. **(Amendment 2026-09-23):** decided by the user on 2026-09-23 — **Option C, reject and quarantine** (see D2). The goal becomes: make unrecovered `GARBLING` **and `NODE_GARBLING`** on the tree route raise `LowQualityTreeError` without calling `save_doc`, after writing the tree and its verdict to an unserved, erasable `quarantine/` prefix. **(Amendment 2026-09-23, iter 2):** "on the tree route" is literal — the override fires only while `state.route == Route.TREE`, so a document that recovery has legitimately moved to `Route.FLAT` (e.g. the tesseract-raster branch of `_recover_vlm_fallback`) is not rejected. Quarantine objects are keyed by the document's `sha256`, not a `doc_id`.
3. Close three editorial drifts (`FLAT-01-C3`, `FLAT-03-C2`, `INDEX-01-C2`) where the contract's substance is right and only its text is stale.
4. Record and ratify the `ERASE-01-C2` idempotency defect and its fix.
5. Harden the contracts gate's own grep so a stale `.pyc` can never mask a deleted contract test.

## Non-Goals

1. **No new contracts.** This RFC amends and ratifies existing contract text; it does not add coverage for behaviour that has no contract today. **(Amendment 2026-09-23):** bounded exception — D2 Option C introduces a new behaviour (the quarantine write) and therefore adds exactly two new clauses: one `ERASE-01` clause (`delete_doc` removes ~~`quarantine/<doc_id>.json` + `.meta.json`~~ `quarantine/<sha256>.json` + `.meta.json` **(Amendment 2026-09-23, iter 2)**, HR2) and one `OCR-01` clause (quarantine is written before the reject is raised). No other new contracts.
2. **No change to `validate_tree`.** Its byte-for-byte stability is an [[RFC-004]] Amendment-1 invariant and neither D2 option touches it.
3. **No re-litigation of the RFC-044 authority model.** D2 Option A changes one `REASON_POLICY` entry; it does not reopen who owns routing. **(Amendment 2026-09-23, iter 2):** under the adopted Option C, `REASON_POLICY` and `decide_route` are **unchanged**; the reject is a post-recovery `force_route` override in `index()`.
4. **No contracts-gate redesign.** D6 is a one-line hardening. The structural weakness — that the gate greps for a string rather than verifying an assertion — is real and is *not* addressed here.
5. **No corpus re-run.** D2 Option A would warrant one; that belongs to whichever option is approved, not to this RFC. **(Amendment 2026-09-23):** with Option C approved, a corpus re-run against [[rfc047-d9-final-baseline]] (via the corpus-score-diff workflow) is part of D2-C execution and is now in scope. **(Amendment 2026-09-23, iter 2):** the revised ~2.5–3 day effort includes this re-run (Wave 3).

## Glossary

| Term | Definition |
|------|------------|
| Contract drift | A contract whose stated `trigger`/`effect` no longer describes production behaviour. Distinct from a missing test, and strictly more dangerous, because a labelled test makes it report green. |
| Editorial drift | Drift in which the contract's *intent* is still correct and only its prose names the wrong function, line, or value set. No code change follows. |
| Probe test | A test written deliberately to the literal contract text, with no reference to the implementation, used to detect drift. Two of the six items below were found this way. **(Amendment 2026-09-23):** the two probe tests were never committed and are not in VCS; they are re-derived as part of D2-C execution. |
| Quarantine **(Amendment 2026-09-23)** | MinIO prefix `quarantine/` holding rejected trees (~~`<doc_id>.json` + `.meta.json`~~ `<sha256>.json` + `<sha256>.meta.json` **(Amendment 2026-09-23, iter 2)**: a rejected document never receives a `doc_id` — `_persist_tree_result` mints it with `uuid4` at `client/indexer.py:2243` and the flat route mints it in `_apply_picture_enrichment` at `client/images.py:205`, both after the reject point — so the key is the content hash computed in `index()` at `indexer.py:2458`) for diagnosis. No MCP query tool reads it, so it is not a served "stored artifact" in HR5's sense ~~;~~ **(iter 2:** the one-clause HR5 amendment proposed in [Task 7.9](../tasks/tasks-rfc049-contract-drift-remediation.md#79-propose-the-claudemd-hr2-purge-list-change) makes this explicit rather than interpretive**)**; it is a derived store for HR2 purposes, expires after a 30-day MinIO lifecycle TTL, and is cleared when the same bytes later persist successfully. |
| Label drift | A test that covers a contract but no longer contains its ID, so the grep-based gate cannot see it. The expected diagnosis for the 22 FAILs; it turned out to be the minority. |

## Requirements

### Requirement 1: No contract may report green against code that contradicts it

**User Story:** As an engineer reading a contract to learn the system's guarantees, I want the contract to describe what the code actually does, so that I do not build on a guarantee that was withdrawn two RFCs ago.

#### Acceptance Criteria

1. WHEN a contract's `effect` text is contradicted by a deliberate, documented code path, THE contract SHALL be amended or the code SHALL be changed — a labelled test that pins the code while the contract says otherwise is not an acceptable resting state.
2. WHILE such a contradiction is unresolved, THE contract's gate entry SHALL be left at FAIL rather than labelled, so the gate's red reflects the open question.
3. WHERE a hard rule in `CLAUDE.md` is the thing contradicted, THE resolution SHALL require explicit human approval and SHALL NOT be taken by an agent.
4. **(Amendment 2026-09-23)** WHEN a contract's text has been amended to match the code, THE existing pinning test SHALL be labelled with the contract ID in the same change or later, never earlier.
5. **(Amendment 2026-09-23 — editorial drift)** Contract trigger/effect text SHALL name the function, value set and path the code exhibits.

**Clarification (Amendment 2026-09-23, decided by the user):** the "never label ahead of amendment" rule (AC2, AC4) bars labels on a contract whose **effect** is contradicted by the code. A contract whose effect is true and asserted but whose **trigger** text is stale may keep an existing label, provided the drift is listed in this RFC. `FLAT-03-C2` is the instance: it is already labelled on `tests/test_flat.py:1529` and passing, its effect is true, and its stale trigger is listed as D4(a). The label stays.

**Carve-out extension (Amendment 2026-09-23, iter 3):** two further contracts already carry a label on a passing test while their **effect** text is wrong. They are `FLAT-01-C3` (`tests/test_helpers_combined.py:524`; the role set omits `image`, listed as D3) and `CONV-01-C5` (`tests/test_converters.py:783`; "never raises" and a hard-coded language set, listed as D4(c)). These pre-existing labels are grandfathered, not new labels added ahead of an amendment. They stay until the Wave 0/1 amendment. **The same PR that lands Wave 0/1 closes the mismatch**, so no release carries a labelled contract whose effect text is known to be wrong. See the design's Property 4.

### Requirement 2: The contracts gate must not be maskable by build artefacts

**User Story:** As a reviewer trusting the gate's PASS count, I want that count to be derived only from source, so that deleting a contract test cannot leave the gate green.

#### Acceptance Criteria

1. WHEN the contracts gate searches `tests/` for a contract ID, THE search SHALL consider only `*.py` source files.
2. THE gate SHALL exclude `__pycache__` directories from the search.

### Requirement 3: HR2 cascade idempotency (Amendment 2026-09-23)

**User Story:** As an operator retrying a right-to-erasure request after a partial failure, I want a clean result when every derivative is already gone, so that `errors == []` remains trustworthy evidence that the HR2 cascade completed.

#### Acceptance Criteria

1. WHEN `delete_doc` is retried after a partial failure, THE cascade SHALL report `errors == []` if every derivative is already absent.
2. A non-`NoSuchKey` `S3Error` SHALL still be surfaced in `errors`.

Validated by `tests/test_storage.py:201` (`test_erase_01_c2_prefix_loops_tolerate_nosuchkey_but_surface_other_errors`). Traces to D5.

### Requirement 4: Rejected garbled trees are quarantined, never served, and erasable (Amendment 2026-09-23)

**User Story:** As an operator, I want an unrecoverably garbled document to fail its job under HR5 while its tree remains inspectable for diagnosis, without that tree becoming a served artifact or an erasure blind spot.

#### Acceptance Criteria

1. WHEN a tree-route document ends GATES recovery with `first_defect` `GARBLING` or `NODE_GARBLING` unrecovered, THE `index()` call SHALL raise `LowQualityTreeError` with that defect's reason string, SHALL increment `LOW_QUALITY_TREES{reason=<reason>}`, and SHALL NOT call `save_doc`. **(Amendment 2026-09-23, iter 2):** "tree-route" means `state.route == Route.TREE` at the override point; the full guard is `not state.ok and state.route == Route.TREE and state.first_defect in {GARBLING, NODE_GARBLING}`. A document that recovery forced to `Route.FLAT` with `ok=False` and a `GARBLING` first defect (the tesseract-raster branch of `_recover_vlm_fallback`, `client/recovery.py:1127-1134`) SHALL NOT be rejected by this override.
2. BEFORE raising (on both the tree route and the ~~existing flat garble guard~~ flat garble reject), THE indexer SHALL write the tree, the verdict and the ~~decision-event trail~~ garble samples to MinIO ~~`quarantine/<doc_id>.json` and `quarantine/<doc_id>.meta.json`~~ `quarantine/<sha256>.json` and `quarantine/<sha256>.meta.json`. **(Amendment 2026-09-23, iter 2):** (a) there is no per-document decision-event collector — `obs/decisions.py:19-58` `decision()` only logs — so the "decision-event trail" is dropped from the payload; the payload is the full tree (or flat blocks) + verdict + garble samples. (b) The flat write happens **inside `_persist_flat_result`**, immediately before its `return None` at `indexer.py:1879-1880`, where `_garble_blocks`, the flat garble report and the `sha256` parameter are in scope. The pre-match guard at `indexer.py:2622-2624` never fires in production (the flag is set only inside `_persist_flat_result`, which runs later, from the `(False, Route.FLAT)` arm at `:2653`); it stays untouched as a defensive guard. (c) `.meta.json` carries `filename`, `job_id` if available, `reason`, `first_defect`, `route` and a UTC timestamp.
3. NO MCP query tool SHALL read from the `quarantine/` prefix. **(Amendment 2026-09-23, iter 2):** enforced by a **mandatory** static test asserting that no read path under `src/` references `"quarantine/"` other than the storage writer/eraser helpers.
4. WHEN `delete_doc` runs, THE HR2 cascade SHALL remove ~~`quarantine/<doc_id>.json`~~ `quarantine/<sha256>.json` and `.meta.json` via `_remove_object_idempotent` (so a missing quarantine object is idempotent success). **(Amendment 2026-09-23, iter 2):** the `sha256` is `ctx.sha256`, discovered by `_erase_verdicts` (sidecar, then registry fallback) earlier in the cascade.
5. **(Amendment 2026-09-23, iter 2)** WHEN a document's bytes persist successfully (`save_doc` in `_persist_tree_result`, `save_flat_doc` in `_persist_flat_result`), THE indexer SHALL idempotently delete `quarantine/<sha256>.json` and `.meta.json` for that `sha256`. Independently, a MinIO lifecycle rule SHALL expire objects under `quarantine/` after 30 days.
6. **(Amendment 2026-09-23, iter 2)** FOR a document that was only ever rejected (no `processed/` sidecar, no registry row, so no `doc_id` to pass to `delete_doc`), an operator SHALL be able to erase its quarantine copy by `sha256` through a storage helper `erase_quarantine(sha256)`, documented in the ARCHITECTURE erasure runbook.
7. **(Amendment 2026-09-23, iter 3) — HR5-critical.** IF the quarantine write fails (on the tree route or the flat route), THEN the document SHALL still be rejected: `LowQualityTreeError` is raised with the same reason, `save_doc` / `save_flat_doc` are **never** called, `QUARANTINE_WRITES_TOTAL{result="error"}` is incremented, and a `quarantine_write` decision event with choice `failed` is emitted. Inspectability never outranks rejection.
8. **(Amendment 2026-09-23, iter 3)** WHEN a job ends with `reason=low_quality_tree`, THE worker parent SHALL add the document's `sha256` to the job's result record (the Redis job hash written by `_set_job_status`), and `GET /upload/status/{job_id}` SHALL return it in the error body. The value SHALL equal the quarantine key: the parent hashes the same staged file that the child indexed, with the same algorithm. The arq return value of the terminal path stays `""`, because it is pinned by `tests/test_worker.py:178` (`FLAT-04-C2`). The `FLAT-04-C2` effect text ("byte-for-byte unchanged") receives an editorial amendment in the same change.

**Clarifications to AC2, AC4, AC5 and AC6 (Amendment 2026-09-23, iter 3):**

- **AC2:** `.meta.json` carries a `filenames: []` array in place of a single `filename`. It is merged read-modify-write on every rewrite, following the `save_doc_meta` pattern (`storage/verdict.py:52-172`), so identical bytes rejected under several names keep every name. The flat payload `_garble_blocks` is a `list[dict]` of plain, JSON-serialisable dicts.
- **AC4 / AC6, one implementation:** the cascade step `_erase_quarantine(ctx)` removes both keys with `_remove_object_idempotent(ctx, key, "quarantine", fmt)` (`storage/documents.py:339-355`). The operator helper `erase_quarantine(sha256) -> list[str]` builds a minimal `ErasureContext(doc_id=f"sha256:{sha256}", mc=…, sha256=sha256)`, calls the same `_erase_quarantine` and returns `ctx.errors`. Both functions live in `pageindex_mcp.storage.documents`. A thin wrapper, `scripts/erase-quarantine.sh <sha256>`, is the operator entry point. The runbook matches filenames against the meta's `filenames` array, or takes the sha256 from the AC8 status body.
- **AC5:** clear-on-success deleting the diagnostic copy when the same bytes later persist is **intended**, because the served `processed/` artifact supersedes it.

Traces to D2 Option C.

## Decision Summary

**Current state (2026-09-23) (Amendment 2026-09-23, iter 3)**. Read this first. It supersedes the iter-2 table, which is preserved verbatim in [Appendix A.3](#a3-iter-2-current-state-table-verbatim-moved-2026-09-23-iter-3). The per-item text below keeps its amendment trail. The design and tasks files were consolidated into a single v2 text in iter 3, with their prior text in an appendix. This RFC stays a decision record. **Status: `accepted`** (approved for build), with `implemented` to be set at close-out (Task 9.1).

| Item | Current state |
|---|---|
| [D1](#d1-lang-01-c2-is-stale--the-raise-is-the-fix-not-the-bug) | Amend `LANG-01-C2` and the `lang-01.yaml` header by script class, then label `tests/test_helpers_combined.py:420`. Wave 1. |
| [D2](#d2-ocr-01-c3-vs-hard-rule-5--the-decision-requires-explicit-approval) | **[Option C](#option-c--reject-and-quarantine-adopted-2026-09-23) adopted** (user, 2026-09-23). Options A and B moved to [Appendix A](#appendix-a-d2-options-a-and-b-verbatim-moved-2026-09-23-iter-3). See the D2-C rows below. |
| D2-C: override | Post-recovery `force_route=Route.REJECT` in `index()` at `~:2601`, with guard `not state.ok and state.route == Route.TREE and state.first_defect in {GARBLING, NODE_GARBLING}`. `vt_raw` = `state.gate_result if state.gate_result is not None else (state.ok, state.reason)` (iter 3; bare `None` raises `TypeError`). `REASON_POLICY` and `decide_route` unchanged. |
| D2-C: quarantine | `quarantine/<sha256>.json` + `.meta.json`. Tree write in `index()`. Flat write inside `_persist_flat_result`, before `return None` (`:1879-1880`). The `:2622` guard is dead, kept and not a write site. Payload: tree/blocks + verdict + garble samples. **`filenames: []`** merged read-modify-write (iter 3). |
| D2-C: write-failure rule | A failed write still rejects: no `save_doc` / `save_flat_doc`, `QUARANTINE_WRITES_TOTAL{result="error"}`, `quarantine_write`=`failed`. `save_quarantine` is the single emitter. HR5-critical; R4 AC7 (iter 3). |
| D2-C: sha256 surfacing | The rejected job's hash and `GET /upload/status` error body carry `sha256`, computed in the worker parent. arq return value unchanged. R4 AC8, Task 7.5d (iter 3). |
| D2-C: retention (Q4) | **Resolved** as storage limitation, not HR3: a 30-day MinIO lifecycle TTL plus clear-on-success (clearing is intended). If bucket versioning is on, a noncurrent-version rule is added (Task 7.5c). |
| D2-C: lifecycle-rule owner | **Salil Trehan** (operator/infra). The repo has no lifecycle configuration today. |
| D2-C: erasure | Cascade step `_erase_quarantine(ctx)` via `_remove_object_idempotent`. Operator `erase_quarantine(sha256)` reuses it through a minimal `ErasureContext`. Both are in `pageindex_mcp.storage.documents`, with `scripts/erase-quarantine.sh`. Prefix guard registered. Five existing erasure tests are updated (Task 7.5). Backup coverage **unverified** (Task 6.2). |
| D2-C: contracts and hard rules | New `OCR-01-C4` and `ERASE-01-C4`. `FLAT-04-C2` gets an editorial note for sha256. The `CLAUDE.md` HR2 + HR5 edit is **pending human approval** (Task 7.9). |
| [D3](#d3-flat-01-c3s-role-set-is-incomplete--add-image) | Editorial: add `image` to `FLAT-01-C3`. The pre-existing label stays under the carve-out extension (R1). Wave 0. |
| [D4](#d4-three-drifted-triggereffect-clauses--substance-intact-text-stale) | (a) Subsumed by D2-C. (b) `INDEX-01-C2` trigger text, Wave 0. (c) `CONV-01-C5` effect text, Wave 1; its pre-existing label stays under the carve-out extension. |
| [D5](#d5-erase-01-c2-idempotency-hole--ratification-fix-already-applied) | Ratified as R3; committed `e2ecd4b`. |
| [D6](#d6-the-contracts-gates-own-grep-is-unhardened--latent-one-line-fix) | One-line grep hardening, then a gate re-run (Risk 4). Wave 0. |
| R3 | Satisfied by D5 (`tests/test_storage.py:201`). Extended by D2-C: `errors == []` on retry also covers `quarantine/`. |
| R4 | AC1–AC6 (iter 2) plus AC7 (write failure) and AC8 (sha256 surfacing), both iter 3. |
| Checkpoints | Today **PASS=64 FAIL=2** → Checkpoint A **65/1** (FAIL = `OCR-01-C3`) → Checkpoint B **68/0** (66 gate lines + `OCR-01-C4`, `ERASE-01-C4`). |
| Effort | D2-C **~2.75–3.25 days** including the Wave 3 corpus re-run (iter 3: +0.25 day for sha256 surfacing and the write-failure tests). Waves 0/1 take ~1 hour. |

Six items. **D2 requires explicit human approval and is presented as two options, unresolved.** **(Amendment 2026-09-23):** D2 was decided by the user on 2026-09-23 — Option C (reject + quarantine), added below. D5 is a ratification of a fix ~~already in the working tree~~ already committed as `e2ecd4b` **(Amendment 2026-09-23)**. D3, D4a, D4b and D4c are editorial. D1 and D6 are small and, in the author's view, uncontroversial — but they are still proposals.

### D1: `LANG-01-C2` is stale — the raise is the fix, not the bug

**Contract:** `agents/contracts/lang-01.yaml`
**Code:** `src/pageindex_mcp/converters/ocr_langs.py`

**The drift.** `LANG-01-C2`'s effect says a missing language "is dropped from the returned set rather than raising", and the file's header comment reinforces it: *"ensure_tessdata NEVER fails hard"*. The code raises `TessdataUnavailableError` at ~~**three** sites~~ **four raise statements across three functions** **(Amendment 2026-09-23: the table below always had four rows)**:

| Site | Function | Condition |
|---|---|---|
| `ocr_langs.py:299` | `_ensure_lang_no_prefix` | non-Latin lang, cached probe says absent |
| `ocr_langs.py:331` | `_ensure_lang_no_prefix` | non-Latin lang, live probe says absent |
| `ocr_langs.py:368` | `_ensure_lang_with_prefix` | `lang not in _LATIN_LANGS` and not provisionable |
| `ocr_langs.py:399` | `_ensure_tessdata_empty_fallback` | nothing survived and a non-Latin lang was requested — *"refusing Latin-only fallback"* |

(Four raise statements across three functions; the two in `_ensure_lang_no_prefix` are the cache-miss and probe-miss branches of the same guard.)

**(Amendment 2026-09-23):** the `ocr_langs.py:399` raise in `_ensure_tessdata_empty_fallback` is **effectively unreachable**: every non-Latin language is either appended to the result or raises inside the per-lang loop at `:299`/`:331`/`:368` before the empty-fallback is reached. The amended contract text must not present `:399` as a live path; the live non-Latin raises are the three in-loop sites.

This is **deliberate and documented**. The `ensure_tessdata` docstring ~~states it under D6/ISS-34~~ (`ocr_langs.py:234-246`, labelled *"(Fix 5)"*; the D6/ISS-34 tag lives in the test docstring at `tests/test_helpers_combined.py:421`) **(Amendment 2026-09-23)** states: *"a missing non-Latin-script language raises `TessdataUnavailableError` instead of being silently dropped, since that would silently degrade OCR to gibberish/empty output for scripts Latin OCR cannot read."* Degrading Arabic to `deu,eng` does not produce less text — it produces confidently wrong text, which is worse, and which the garble detectors then have to catch downstream. The raise *is* the fix.

**Proposed amended effect.** Split the guarantee by script class:

> For each missing language, `_try_download_tessdata` attempts a fetch only when `TESSDATA_ALLOW_DOWNLOAD` permits network egress. A missing **Latin-script** language (a member of `_LATIN_LANGS`) is dropped from the returned set with a logged degradation, and the returned list falls back to at least `['deu','eng']` rather than being empty. A missing **non-Latin-script** language raises `TessdataUnavailableError` rather than degrading, because Latin-only OCR on a non-Latin script yields gibberish rather than less text (D6/ISS-34); each caller that can proceed without that script catches the error and degrades explicitly at its own call site.

**Note on the catch sites.** The triage report named "the indexer image branch" as the catcher. There are **five**, and this correction matters for the amended boundary text:

- `client/indexer.py:1262` — standalone-image branch; degrades to `['deu','eng']` and emits a `standalone_image_tessdata_availability` decision event.
- `client/indexer.py:1317` — the RFC-046 D4 bounded detect-correct-retry; degrades by *keeping the original* `img_langs`, not to `deu,eng`.
- `client/images.py:137` — degrades to `['deu','eng']` with a warning.
- `converters/pictures.py:1644`.
- `client/recovery.py:436`.

**(Amendment 2026-09-23):** `images.py:137`, `pictures.py:1644` and `recovery.py:436` all degrade to `['deu','eng']` with a logged warning.

Uncaught, the error is terminal by design: `worker/errors.py:30` classifies `TessdataUnavailableError` as `converter_env_missing`, `terminal=True`.

**Test.** Both halves are already discharged by `tests/test_helpers_combined.py:420::test_ensure_tessdata_non_latin_raises_latin_degrades`, which asserts `pytest.raises(TessdataUnavailableError, match="ara")`, `ensure_tessdata(["fra"]) == ["deu","eng"]`, and the pre-baked no-op. **Once this amendment lands, the `LANG-01-C2` label should be placed on that test** and the gate's first remaining FAIL clears. Labelling it *before* the amendment would be exactly the failure mode Requirement 1 forbids.

**Also amend the file header comment** ("ensure_tessdata NEVER fails hard") in the same edit; it is the same claim in a second place. **(Amendment 2026-09-23):** that comment lives in the YAML header of `agents/contracts/lang-01.yaml:9-11`, not in `ocr_langs.py`; amend it there.

### D2: `OCR-01-C3` vs Hard Rule 5 — THE DECISION. Requires explicit approval.

~~**Status: OPEN. Both options are presented. This item must not be actioned by an agent.**~~

**Status: DECIDED 2026-09-23 by the user: Option C (reject + quarantine).** **(Amendment 2026-09-23)** The decision was taken by the user in an `/rfc-iterate` session; Options A and B are retained below unchanged for the record. Execution of Option C is now in scope; the CLAUDE.md HR2 purge-list edit it implies still requires separate human approval.

→ **See [Option C](#option-c--reject-and-quarantine-adopted-2026-09-23)** for the adopted mechanism. **(Amendment 2026-09-23, iter 2):** the pending human approval now covers one bundled `CLAUDE.md` edit — the HR2 purge list and a one-clause HR5 amendment — tracked as [Task 7.9](../tasks/tasks-rfc049-contract-drift-remediation.md#79-propose-the-claudemd-hr2-purge-list-change).

**Contract:** `agents/contracts/ocr-01.yaml` (`OCR-01-C3`)
**Hard Rule:** `CLAUDE.md` #5 — *"Never silently persist a low-quality tree. `validate_tree()` must run before `save_doc`; a failing tree must surface as an arq `low_quality_tree` error, not a stored artifact."*

#### What the code does

`REASON_POLICY[TreeDefect.GARBLING]` is `_ReasonPolicy.RETRY_OCR` (`helpers/gates.py:581-589`; **(Amendment 2026-09-23):** `REASON_POLICY` is built from `GATES` at `gates.py:680`). `decide_route` maps `RETRY_OCR → Route.TREE` unconditionally (~~`helpers/types.py:369-370`~~ `helpers/types.py:369` lookup, `:372-373` `RETRY_OCR` branch **(Amendment 2026-09-23)**; the *"retry handled upstream"* comment is in the docstring at `:358`). So an unrecovered garbling on the tree route arrives at the route dispatch in `index()` as `(ok=False, route=Route.TREE)` and hits this case (~~`client/indexer.py:2557-2563`~~ `client/indexer.py:2693-2699` **(Amendment 2026-09-23)**):

```python
case (False, Route.TREE) | (False, Route.PERSIST_FAIL):
    logger.warning(
        "Persisting low-quality tree with FAIL verdict for %s: reason=%s",
        filename, state.reason,
    )
    # fall through to _persist_tree_result below
```

`_persist_tree_result` calls `save_doc`. `LowQualityTreeError('garbling')` is never raised on this path. **(Amendment 2026-09-23):** `_persist_tree_result` is defined at `indexer.py:2228` and called at `:2713`.

**(Amendment 2026-09-23) — `NODE_GARBLING`, previously unmentioned.** `GATES` in `helpers/gates.py` also carries `GateSpec(TreeDefect.NODE_GARBLING, _ReasonPolicy.RETRY_OCR, ...)` with the same recovery functions, so an unrecovered `NODE_GARBLING` reaches the same `(False, Route.TREE)` case and is persisted identically. The user decided that Option C applies identically to `NODE_GARBLING` (reject + quarantine, reason string per its existing defect value).

The guarantee survives **only on the flat route**, via a pre-match guard at ~~`client/indexer.py:2488`~~ `client/indexer.py:2622-2624` (decision event at `:2612`) **(Amendment 2026-09-23)**:

```python
if state.flat_garble_unrecovered:
    LOW_QUALITY_TREES.labels(reason="garbling").inc()
    raise LowQualityTreeError("garbling")
```

`state.flat_garble_unrecovered` is set at ~~`indexer.py:1644`~~ `indexer.py:1778` **(Amendment 2026-09-23)** inside `_persist_flat_result` (defined `:1700`; flag reset at `:1744`/`:1757`), from `_garble_check_flat_blocks`, and short-circuits that function at ~~`:1745`~~ `:1879-1880` **(Amendment 2026-09-23)** (`return None`) before enrichment. So the same defect — unrecovered garbling — rejects on the flat route and persists with a FAIL verdict on the tree route. That asymmetry is the substance of this item.

**(Amendment 2026-09-23, iter 2) — the pre-match guard is dead code; the flat reject happens elsewhere.** `_persist_flat_result` has one caller: the `(False, Route.FLAT)` arm at `indexer.py:2653`, which runs **after** the guard at `:2622`. The flag is reset to `False` inside `_persist_flat_result` (`:1744`, `:1757`) and set to `True` only at `:1778`, so when the guard at `:2622` is evaluated the flag is always `False`. The flat garble reject that actually fires is: `_persist_flat_result` returns `None` at `:1879-1880`, and the `(False, Route.FLAT)` arm then computes `_reject_reason = "garbling"`, increments `LOW_QUALITY_TREES` and raises `LowQualityTreeError` (`~:2667-2681`). The asymmetry finding stands; only the location of the live flat raise changes. The `:2622` guard is left untouched as a defensive guard.

**(Amendment 2026-09-23) — nuance on the asymmetry.** Recovery can itself move a document to the flat route: `client/recovery.py` calls `finalize_gate_and_route(..., force_route=Route.FLAT)` at `:955`, `:1133`, `:1214` and `:1298`. The asymmetry therefore applies only where recovery leaves the route at `TREE`.

**One correction to the triage report:** `persist_tree_with_fail_verdict` is not a function. It is the `choice` string of the `persistence_route_dispatch` decision event (~~`indexer.py:2500-2501`~~ choice map `indexer.py:2634-2635`, event emitted `:2638` **(Amendment 2026-09-23)**, declared in ~~`obs/decision_points.py:1006`~~ `obs/decision_points.py:997` (event), `:1006` (choice) **(Amendment 2026-09-23)**). The actual call is the fall-through to `_persist_tree_result`.

#### Evidence, stated as evidence

- An agent wrote a probe test **literally to `OCR-01-C3`** — no reference to the implementation. It failed on **all three** of the contract's stated triggers (still-garbled after retry; `OCR_ESCALATION` disabled; exception during the retry), with: *"index() returned instead of rejecting; save_doc was called on a rejected tree."*
- **Independently**, a second agent wrote a probe test literally to `FLAT-03-C2`. It failed with `DID NOT RAISE`.
- Two agents, two entry points, one conclusion. This is two probe tests, not a corpus measurement — but it is not a single agent's single observation either.
- **(Amendment 2026-09-23):** neither probe test is in the repository — they were never committed and are **not in VCS**. They will be re-derived, written literally to the contract text, as part of D2-C execution, and labelled `OCR-01-C3` / `FLAT-03-C2` once passing.
- **(Amendment 2026-09-23) — correction on the "exception during the retry" trigger.** The RFC implied that a retry exception propagates. It does not: `_execute_ocr_retry` (`client/recovery.py`) swallows retry exceptions in its own `except Exception` — it logs, increments `OCR_ESCALATION_TOTAL{result="error"}`, emits `ocr_retry_escalation_outcome`, and returns `False`. With escalation disabled the retry never fires. In all three trigger cases the observable end state is the same: after the GATES recovery loop, `state.ok` is `False` with `first_defect` `GARBLING`. All three triggers therefore collapse to **one post-loop check**.

**Options A and B (Amendment 2026-09-23, iter 3):** not adopted. Their full text is in [Appendix A](#appendix-a-d2-options-a-and-b-verbatim-moved-2026-09-23-iter-3), moved verbatim and not deleted. In one line: A restores HR5 through a `REASON_POLICY` / `decide_route` change, measured blast radius 0 documents; B ratifies RFC-030's persist-with-FAIL position by amending HR5.

#### Option C — reject and quarantine (ADOPTED 2026-09-23)

**(Amendment 2026-09-23)** Decided by the user on 2026-09-23. Option C is Option A with the inspectability cost removed by quarantine rather than by an error payload.

- **Mechanism.** Unrecovered `GARBLING` — and, identically, `NODE_GARBLING` — on the tree route, once recovery is exhausted, is forced to `Route.REJECT` by a **post-recovery override** placed in `index()` **after the GATES recovery loop and before `_recover_flat_prefer`**. ~~It reuses the existing `finalize_gate_and_route(..., force_route=Route.REJECT)` pattern already used at the `client/recovery.py` override sites.~~ **(Amendment 2026-09-23, iter 2):** it is a **new use of an existing API**: `finalize_gate_and_route` (`helpers/types.py:411`) accepts `force_route`, but no site forces `Route.REJECT` today — the four override sites (`client/recovery.py:955`, `:1133`, `:1214`, `:1298`) all force `Route.FLAT`. The call passes `state.gate_result` as the required `vt_raw` argument (the legacy tuple form emits a `DeprecationWarning`). **(Amendment 2026-09-23, iter 3) — correction, blocking:** passing a bare `state.gate_result` is wrong. It starts as `None` (`ExtractionState`, `helpers/types.py:219`), and the legacy branch of `finalize_gate_and_route` resets it to `None` (`helpers/types.py:459`). A `None` `vt_raw` takes the legacy branch and raises `TypeError`. The call must mirror the existing override sites (`client/recovery.py:947`, `:1125-1126`, `:1206`, `:1290`): `finalize_gate_and_route(state, state.gate_result if state.gate_result is not None else (state.ok, state.reason), settings.flat_doc_routing, force_route=Route.REJECT)`. The tuple form's `DeprecationWarning` is accepted, as it is at those sites. `finalize_gate_and_route` then sets `state.reason = str(vt_raw)`. That is harmless, because the REJECT arm computes its reason from `state.first_defect.value` (`client/indexer.py:2683-2684`).
  - **Guard (iter 2).** The condition is `not state.ok and state.route == Route.TREE and state.first_defect in {TreeDefect.GARBLING, TreeDefect.NODE_GARBLING}`. The `route == TREE` clause is load-bearing: the tesseract-raster branch of `_recover_vlm_fallback` (`client/recovery.py:1127-1134`) calls `finalize_gate_and_route(..., force_route=Route.FLAT)` without `force_ok`, so it leaves `ok=False` with a `GARBLING` first defect. That is a legitimate recovery to the flat route, and a guard without `route == TREE` would cancel it.
  - **Insertion point (iter 2).** The GATES loop **starts** at `indexer.py:2561` (`for _gate in GATES:`) and its body ends at `~:2592`; the `no_gate_eligible` decision follows at `~:2594-2600`. The override goes between `~:2601` and `:2604`, before `_recover_flat_prefer` (`:2604`) and `_recover_landscape_reroute` (`:2605`). Both of those return early unless `state.ok` (`client/recovery.py:1171-1172`, `:1255`), so neither can move a not-ok document off `TREE` and the ordering does not change their behaviour.
- **What it must not touch.** It does **not** change `REASON_POLICY[GARBLING]` / `REASON_POLICY[NODE_GARBLING]` or `decide_route`. `finalize_gate_and_route` has ~19 callers, including mid-retry recovery functions where `RETRY_OCR → TREE` is load-bearing; a policy-table change would reject documents mid-recovery. **(Amendment 2026-09-23, iter 2):** "~19" is the graph in-degree including tests; there are **7 production call sites** — `client/indexer.py:611`, `:1556`; `client/recovery.py:897`, `:949`, `:1127`, `:1208`, `:1292`.
- **Effect.** `index()` raises `LowQualityTreeError('garbling')` (or the `NODE_GARBLING` defect's existing reason string); `save_doc` is never called; `LOW_QUALITY_TREES{reason=<reason>}` increments. ~~**HR5 holds as written.**~~ **(Amendment 2026-09-23, iter 2):** HR5's operative requirement — a failing tree surfaces as an arq `low_quality_tree` error and is never served — holds. Whether an unserved `quarantine/` copy is a "stored artifact" is interpretive under the current wording, so a one-clause HR5 amendment is proposed with the HR2 edit under human-approved [Task 7.9](../tasks/tasks-rfc049-contract-drift-remediation.md#79-propose-the-claudemd-hr2-purge-list-change): *"…must surface as an arq `low_quality_tree` error, not a stored artifact served by any query tool; an unserved, erasable `quarantine/` copy kept for diagnosis is permitted."* `OCR-01-C3` and `FLAT-03-C2` become true as written, so **D4(a) is subsumed**. **(Amendment 2026-09-23, iter 3):** the proposed HR5 clause is revised, still subject to human approval only: *"…not a stored artifact reachable through the MCP query surface or any HTTP route; an unserved `quarantine/` copy, purged by `delete_doc` and expiring within 30 days, is permitted for diagnosis."* The HR2 list-all-manifest-stores edit is unchanged.
- **One check covers all three `OCR-01-C3` triggers.** Because `_execute_ocr_retry` swallows retry exceptions and escalation-disabled never fires the retry (see Evidence), all three triggers end with `state.ok == False` and `first_defect == GARBLING` after the loop; the single post-loop override handles them all.
- **Quarantine.** Before raising, the indexer writes the tree + verdict + ~~decision-event trail~~ to MinIO ~~`quarantine/<doc_id>.json` (+ `quarantine/<doc_id>.meta.json`)~~. No MCP query tool reads `quarantine/`, so it is not a served "stored artifact" in HR5's sense. **The same quarantine write is added to the ~~existing flat guard (`indexer.py:2622-2624`)~~ flat reject path**, removing the flat/tree asymmetry rather than documenting it.
  - **(Amendment 2026-09-23, iter 2) Key.** `quarantine/<sha256>.json` + `quarantine/<sha256>.meta.json`. `doc_id` does not exist at either reject site (it is minted only on the success paths, `indexer.py:2243` and `client/images.py:205`). `sha256` is the local computed in `index()` at `indexer.py:2458` (`sha256 = hashlib.sha256(file_bytes).hexdigest()`), in scope at the tree override, and passed as the `sha256` parameter of `_persist_flat_result`, in scope at its `return None` (`:1879-1880`).
  - **(iter 2) Payload.** Full tree (tree route: `state.result["structure"]`; flat route: `_garble_blocks`) + verdict (`state.gate_result`, or the flat garble report) + garble samples (fired prongs, garble ratio and a bounded set of truncated excerpts from the worst nodes/blocks). The "decision-event trail" is **dropped**: `obs/decisions.py:19-58` `decision()` only logs, and there is no per-document collector to serialise. `.meta.json` carries `filename`, `job_id` if available, `reason`, `first_defect`, `route` and a UTC timestamp.
  - **(iter 2) Flat site.** The write goes **inside `_persist_flat_result`**, before `return None` at `:1879-1880`, where `_garble_blocks` (a local of that method) and `sha256` exist. The pre-match guard at `:2622-2624` is dead in production (see [What the code does](#what-the-code-does)); it is left untouched.
  - **(iter 2) Retention.** A 30-day MinIO lifecycle TTL on `quarantine/`, plus an idempotent delete of `quarantine/<sha256>.*` when the same bytes later persist successfully (after `save_doc` in `_persist_tree_result` and after `save_flat_doc` in `_persist_flat_result`). No lifecycle configuration exists in the repo today (no `set_bucket_lifecycle` / `mc ilm` anywhere); the rule is an operator step documented through [Task 7.8](../tasks/tasks-rfc049-contract-drift-remediation.md#78-update-architecturemd) and tracked by [Task 7.5c](../tasks/tasks-rfc049-contract-drift-remediation.md#75c-configure-the-quarantine-lifecycle-ttl).
  - **(Amendment 2026-09-23, iter 3) Meta, write failure, surfacing.** `.meta.json` holds a `filenames: []` array, merged read-modify-write on every rewrite (pattern: `save_doc_meta`, `storage/verdict.py:52-172`). A failed quarantine write still rejects (R4 AC7). The single emitter of `quarantine_write` and `QUARANTINE_WRITES_TOTAL` is `save_quarantine` in `pageindex_mcp.storage.documents`, registered as `DecisionPoint(event="quarantine_write", phase=Phase.PERSIST, module="pageindex_mcp.storage.documents", function="save_quarantine", choices=("ok","failed"), …)`, because `DecisionPoint` (`obs/decision_points.py:74-108`) requires `phase`, `module` and `function`. The rejected job's status carries the `sha256` (R4 AC8). The worker parent hashes the staged file in `process_document_job`'s `ConverterChildError` branch (`worker/job.py:242-278`), and `GET /upload/status` (`upload_app.py:190-203`) returns the whole job hash.
  - **(iter 2) Erasure.** `_erase_quarantine` reads `ctx.sha256` (set by `_erase_verdicts` from the sidecar, falling back to the registry) and runs after `_erase_meta_json`, before `_erase_redis_cache`. A document that was only ever rejected has no `doc_id`, so an operator helper `erase_quarantine(sha256)` covers it ([Task 7.5b](../tasks/tasks-rfc049-contract-drift-remediation.md#75b-add-the-operator-erasure-path-by-sha256)).
- **Why quarantine instead of an error payload.** Attaching the diagnostic payload to the raised error is not cheaply feasible: `LowQualityTreeError.__init__(self, reason)` (`helpers/types.py:593-601`) carries no payload, and the worker's subprocess boundary drops instance attributes — `worker/errors.py` `_CHILD_ERROR_REGISTRY` classifies by class-name string, and `worker/subprocess_mgr.py` `ConverterChildError` carries only `returncode`, `stderr_tail` and `error_class`. The quarantine write happens **inside the child, before the raise**, so nothing needs to cross the boundary.
- **Costs.**
  - Hard Rule 2's erasure cascade gains a step: `_erase_quarantine`, routed through `_remove_object_idempotent`.
  - Two new contract clauses: one `ERASE-01` (quarantine removed by `delete_doc`) and one `OCR-01` (quarantine written before reject). This is a bounded exception to Non-Goal 1.
  - `ARCHITECTURE.md` MinIO layout table gains a `quarantine/` row.
  - `CLAUDE.md` HR2's purge list gains `quarantine/` — a **human-approved** `CLAUDE.md` edit, listed as a task, not done by this RFC.
  - Quarantine retention/TTL for PII-bearing content is a new Open Question (Q4). **(Amendment 2026-09-23, iter 2):** resolved — 30-day lifecycle TTL + clear-on-success.
  - Documents that today complete with a FAIL verdict fail the arq job instead; measured blast radius on the current corpus is zero (see Option A).
  - **(Amendment 2026-09-23, iter 2)** The HR2 import-time guard must learn the new prefix: `register_storage_prefix("quarantine/")` (`storage/documents.py:35`, registrations at `:48-53`), a `"quarantine/": ("quarantine",)` entry in `_PREFIX_TO_ERASURE_STEPS` (`:689-695`), and an `ErasureStep(name="quarantine", …, execute=_erase_quarantine)` in `_ERASURE_MANIFEST` (`:594-676`); otherwise `validate_erasure_manifest()` (`:698-755`, called at `:759`) raises `ImportError`.
  - **(iter 2)** A new metric `QUARANTINE_WRITES_TOTAL{result=ok|error}` (`metrics/definitions.py`, re-exported from `metrics/__init__.py`) and a new registered decision point `quarantine_write` (`obs/decision_points.py`).
  - **(iter 2)** The `CLAUDE.md` edit bundles HR2 (full cascade list) and a one-clause HR5 amendment, both human-approved (Task 7.9).
- **Effort.** ~1.5–2 days: Option A's ~1 day + ~0.5–1 day for the storage helper, the cascade step, the ERASE test and the layout row. **(Amendment 2026-09-23, iter 2):** revised to **~2.5–3 days including the Wave 3 corpus re-run** — adds the `sha256` keying and operator erasure path, the lifecycle TTL operator step, clear-on-success, the prefix guard, metric and decision-point registration, and the extra probes (route guard, raster-to-FLAT, static no-reader test). **(Amendment 2026-09-23, iter 3):** **~2.75–3.25 days**, i.e. +0.25 day for sha256 surfacing (Task 7.5d) and the two write-failure tests.

#### Recommendation (superseded by Option C)

**(Amendment 2026-09-23, iter 2):** heading was "Recommendation (a recommendation, not a decision)"; renamed because the recommendation below is superseded by the adopted [Option C](#option-c--reject-and-quarantine-adopted-2026-09-23). No inbound link used the old anchor. Text retained for the record.

**Option A**, on three grounds: the measured blast radius is zero documents; it is the only option that leaves `OCR-01-C3` and `FLAT-03-C2` true as written; and it is the only one that removes the route asymmetry rather than documenting it. Option B's inspectability argument is real, and can be recovered under A by attaching the tree's diagnostic payload to the raised error — but that is an addition to A, not a reason to prefer B.

**This is explicitly not settled here.** Hard Rule 5 is a `CLAUDE.md` hard rule; amending it, or changing the code so it becomes true again, is a human decision. Until it is taken, `OCR-01-C3` stays at FAIL in the contracts gate.

**(Amendment 2026-09-23):** superseded — the user adopted **Option C**, which keeps this recommendation's substance (reject under HR5, remove the asymmetry) and replaces its error-payload mitigation with quarantine. `OCR-01-C3` stays at FAIL until D2-C lands and the re-derived probe test passes.

### D3: `FLAT-01-C3`'s role set is incomplete — add `image`

**Contract:** `agents/contracts/flat-01.yaml` (`FLAT-01-C3`)
**Code:** `src/pageindex_mcp/helpers/flat.py`

`FLAT-01-C3` states every block carries a role in `{title, prose, kv, table}`. `route_and_extract_flat` emits a fifth:

- `flat.py:117` — `{"role": "image", "index": fig_index}` from `_FLAT_FIGURE_RE` (`^\[Figure:\s*fig-(\d+)...\]$`), optionally carrying `ocr_text` (from `_FLAT_CHART_TEXT_RE`) and `description`.
- `flat.py:127` — `{"role": "image"}` from `_FLAT_RAW_IMAGE_RE` (`^<!--\s*image\s*-->$`).

`block_text` has a matching `if role == "image":` branch at `flat.py:247`. The full emitted set is exactly `{title, prose, kv, table, image}` — verified by enumerating every `"role"` literal in the module.

**(Amendment 2026-09-23):** `flat.py` itself emits `title`/`prose`/`kv`/`image`; the `table` role literal lives in `helpers/tables.py:79` (and `helpers/table_stitch.py:69`). "Every `\"role\"` literal in the module" should read "across `flat.py`, `tables.py` and `table_stitch.py`". The full set `{title, prose, kv, table, image}` is unchanged.

**Correction to the triage report:** these are at lines **117 and 127**, not ~534-566. The line numbers given were wrong; the finding was right.

**Fix.** Amend the effect to `{title, prose, kv, table, image}`.

**This is editorial, and it is already known to the test.** `tests/test_helpers_combined.py:524::test_flat_01_c3_roles_are_typed_and_gate_independent` asserts against `allowed = {"title","prose","kv","table","image"}` and its docstring already apologises for the gap: *"(plus the later-added 'image' role, which the contract text predates)"*. The amendment lets that parenthesis be deleted. No code change, no behaviour change.

### D4: Three drifted trigger/effect clauses — substance intact, text stale

All three are **editorial**. None implies a code change.

**(a) `FLAT-03-C2`'s trigger.** It says `validate_tree()` returning `(False, 'garbling')` inside `index()` makes `index()` raise. It does not — see D2. The surviving terminal raise is the post-recovery **flat** garble gate: `_garble_check_flat_blocks` sets `state.flat_garble_unrecovered` (~~`indexer.py:1644`~~ `indexer.py:1778` **(Amendment 2026-09-23)**), and the pre-match guard at ~~`indexer.py:2488`~~ `indexer.py:2622-2624` **(Amendment 2026-09-23)** raises `LowQualityTreeError("garbling")` and increments `LOW_QUALITY_TREES{reason=garbling}`. **(Amendment 2026-09-23, iter 2):** that guard is dead in production; the live flat raise is in the `(False, Route.FLAT)` arm (`~:2667-2681`) after `_persist_flat_result` returns `None` at `:1879-1880` — see [What the code does](#what-the-code-does). Moot for the text change, since D4(a) is subsumed.

Reword the trigger to name that path. **The effect clause is correct and is now asserted** by `tests/test_flat.py:1529`. Note the dependency: if D2 resolves to Option A, this trigger becomes true as originally written and D4(a) is subsumed; if D2 resolves to Option B, D4(a) must land. **D4(a) is therefore blocked on D2** and should not be applied independently. **(Amendment 2026-09-23):** D2 resolved to Option C, which (like A) makes the original trigger true — **D4(a) is subsumed** and needs no text change once D2-C lands. The existing `FLAT-03-C2` label on `tests/test_flat.py:1529` stays meanwhile (see the Requirement 1 clarification).

**(b) `INDEX-01-C2`'s trigger.** It says the `_run_page_index` fallback fires when "`pdf_to_markdown` raises an exception". The real trigger is **full failure of the `pdf_markdown_converters()` chain**: the chain is built at `indexer.py:830`, and the legacy fallback sits in the `else` arm at `indexer.py:1192-1205`, which increments `PDF_EXTRACT_FALLBACKS`, emits a `pdf_conversion_outcome` decision with choice `all_converters_failed_legacy_fallback`, and calls `self._run_page_index_retrying(file_path)`. No single converter raising is sufficient; the whole chain must fail. `CONV-01-C1` already words this correctly ("on full-chain failure falls back to legacy page_index") — `INDEX-01-C2` should be brought into line with its sibling. Covered by `tests/test_converters.py:1270`.

**(c) `CONV-01-C5`'s effect.** Two errors:

1. It repeats the D1 claim verbatim — *"(never raises; falls back to `['deu','eng']` if nothing is available)"*. Same amendment as D1 applies: it never raises **for Latin**; for non-Latin it raises and the **caller** degrades. Get these two into one form of words.
2. It names a hardcoded `ensure_tessdata(['ara','deu','eng'])` "superset language set (no pre-existing text layer exists to sample for language detection)". The code does not do this. `indexer.py:1258` calls `detected = detect_ocr_langs(filename)` and then `ensure_tessdata(detected)` — the **filename** is the sample. The degradation to `['deu','eng']` happens in the `except TessdataUnavailableError` handler at `:1262`, not inside `ensure_tessdata`. The parenthetical rationale is also wrong: a language signal *is* sampled, just from the filename rather than a text layer.

Covered by `tests/test_converters.py:783`.

### D5: `ERASE-01-C2` idempotency hole — RATIFICATION (fix already applied)

**Contract:** `agents/contracts/erase-01.yaml` (`ERASE-01-C2`) · **Hard Rule 2** path.
**Code:** `src/pageindex_mcp/storage/documents.py`

**The defect.** Every step of the erasure cascade routes through `_remove_object_idempotent` (`documents.py:339`), which treats `S3Error.code == "NoSuchKey"` as idempotent success — **except two**, the prefix-iterating loops `_erase_uploads` (`:358`) and `_erase_figures` (~~`:405`~~ `:415` **(Amendment 2026-09-23:** `:405` is `_erase_processed_flat_json`**)**). Those called `ctx.mc.remove_object` bare inside a wholesale `except S3Error` that appended to `ctx.errors`. Consequence: after a partial failure, a retry finds the objects still **listed** but already **purged**, so `remove_object` raises `NoSuchKey`, and two already-clean stores are reported as erasure failures. `delete_doc` could never reach a clean `errors == []` — which under HR2 is the only evidence that the cascade completed.

`test_erase_01_c2_idempotent_on_missing_doc` (~~`tests/test_storage.py:184`~~ def at `tests/test_storage.py:183` **(Amendment 2026-09-23)**) misses it because it stubs `list_objects` to `[]`, so those loops never reach `remove_object` at all.

**Severity, honestly.** Real S3 returns 204 on a missing key, so this most likely only fires on a concurrent-delete race or a non-conforming backend. It is a correctness hole in a HR2 path, not an observed production failure.

**What was actually done** (~~verified against the working tree at time of writing; both files are modified, uncommitted~~ **(Amendment 2026-09-23):** committed as `e2ecd4b` — *"fix(storage): tolerate NoSuchKey in the two prefix-iterating erasure steps (HR2)"*; the working tree is clean. Tolerance blocks are at `documents.py:379-386` and `:426-430`):

- `src/pageindex_mcp/storage/documents.py` — both loops now wrap `remove_object` in a `try/except S3Error` that re-raises unless `getattr(e, "code", "") != "NoSuchKey"`, and `continue`s past the `removed += 1` / `fig_removed += 1` counter on tolerance, so a tolerated miss is not counted as a removal. Each carries a comment tying the tolerance to `ERASE-01-C2` and to `_remove_object_idempotent`. The enclosing `except S3Error` that populates `ctx.errors` is unchanged, so genuine errors still surface.
- `tests/test_storage.py` — a new `test_erase_01_c2_prefix_loops_tolerate_nosuchkey_but_surface_other_errors` stubs `list_objects` to return a real `uploads/` object and a real `figures/` object, then runs `delete_doc` twice: once with `remove_object` raising `NoSuchKey` (asserts `errors == []`) and once with a different `S3Error` (asserts the `uploads/` failure is still reported). Its docstring states explicitly why the sibling test cannot see this.

This item is recorded for **ratification**, not for scheduling. Nothing further is proposed. **(Amendment 2026-09-23):** ratified as Requirement 3.

### D6: The contracts gate's own grep is unhardened — latent, one-line fix

**Code:** ~~`scripts/gates/contracts.sh:141`~~ `scripts/gates/contracts.sh:142` **(Amendment 2026-09-23)**

```bash
GREP_HITS=$( (grep -r "$cid" "$REPO_ROOT/tests/" 2>/dev/null || true) | wc -l | tr -d ' ')
```

No `--include='*.py'`, no `--exclude-dir=__pycache__`. Contract IDs live in test docstrings, and docstrings are compiled into `.pyc` bytecode. `grep -r` reports a binary match as one line, which `wc -l` counts as a hit. A contract test that is **deleted** therefore continues to report PASS for as long as its stale `.pyc` survives — ~~the exact shape recorded in [[contracts-gate-greps-bytecode]]~~ **(Amendment 2026-09-23: that wikilink resolved nowhere; the shape inline:)** `tests/test_x.py` containing `"""... FOO-01-C1 ..."""` is deleted; `tests/__pycache__/test_x.cpython-312.pyc` still contains the ID string in its constant pool; `grep -r` prints `Binary file ... matches`; `wc -l` counts 1; the gate reports `[PASS] contracts[FOO-01-C1]`.

**Verified NOT currently distorting results.** ~~`tests/__pycache__/` presently holds only `conftest`, `__init__` and `_garble_compat` bytecode; no contract-ID-bearing test module is cached, and the gate returns identical counts (PASS=64 FAIL=2) with and without bytecode present.~~ **(Amendment 2026-09-23):** the pycache inventory claim was wrong — there are 133 `.pyc` files under `tests/`, including test modules. Corrected evidence: a simulated hardened grep (`--include='*.py' --exclude-dir=__pycache__`) over all 61 contract IDs yields the **identical FAIL set** `{LANG-01-C2, OCR-01-C3}`. Also confirmed: `tests/TEST_INDEX.yaml`, `TEST_BUDGET.baseline` and `golden_files/*.json` contain zero contract IDs, so `--include='*.py'` drops no legitimate hit. **This is a latent defect, not an active one**, and it should be described that way. The 22→2 remediation above was not affected by it.

**Fix.** Add `--include='*.py' --exclude-dir=__pycache__` to that one `grep -r`. Requirement 2.

## Implementation Plan

### Sequencing

**(Amendment 2026-09-23, iter 3):** this RFC keeps a single sequencing table, the current one below. The original pre-decision table has been moved verbatim to [Appendix A.2](#a2-original-sequencing-table-verbatim-moved-2026-09-23-iter-3).

**(Amendment 2026-09-23) — revised sequencing after the D2 decision.** Rows 6–8 above are superseded by the rows below; a gate re-run row is added after D6 (Risk 4). *(iter 3: "above" now refers to the original table in Appendix A.2.)*

| Order | Item | Blocked by | Nature |
|---|---|---|---|
| 1 | D6 gate hardening | — | one line, `scripts/gates/contracts.sh:142` |
| 1a | Re-run the contracts gate after D6 and record PASS/FAIL counts | D6 | verification (Risk 4); expected unchanged FAIL set `{LANG-01-C2, OCR-01-C3}` |
| 2 | D3 `FLAT-01-C3` role set | — | contract text only |
| 3 | D4(b) `INDEX-01-C2` trigger | — | contract text only |
| 4 | D1 `LANG-01-C2` + `lang-01.yaml` header comment | — | contract text; then label the existing test |
| 5 | D4(c) `CONV-01-C5` effect | D1 (shared wording) | contract text only |
| 6 | **D2 decided — Option C** (2026-09-23, by the user) | — | done |
| 7 | **D2-C execution** | 6 | post-recovery `force_route=Route.REJECT` override in `index()` for `GARBLING` + `NODE_GARBLING` **(iter 2: guarded by `route == TREE`)**; ~~`quarantine/<doc_id>.json`~~ `quarantine/<sha256>.json` + `.meta.json` write in tree and flat reject paths **(iter 2: flat write inside `_persist_flat_result`; clear-on-success; 30-day lifecycle TTL; `quarantine/` prefix guard; operator `erase_quarantine(sha256)`; `QUARANTINE_WRITES_TOTAL` + `quarantine_write` registration; `CLAUDE.md` edit now HR2 + HR5 — Amendment 2026-09-23, iter 2)**; `_erase_quarantine` HR2 cascade step via `_remove_object_idempotent`; new `ERASE-01` and `OCR-01` clauses; `ARCHITECTURE.md` MinIO layout row; `CLAUDE.md` HR2 purge-list edit (**pending human approval**); re-derive probe tests literally to contract text and label `OCR-01-C3` / `FLAT-03-C2` once passing; corpus re-run vs [[rfc047-d9-final-baseline]] via the corpus-score-diff workflow. **(Amendment 2026-09-23, iter 3):** `vt_raw` fallback on the override; `filenames[]` meta; write-failure rule (R4 AC7); sha256 surfaced on the rejected job's status (R4 AC8, Task 7.5d, before Checkpoint B); five existing erasure tests updated; `scripts/erase-quarantine.sh`; bucket versioning and backup checks |
| 8 | D4(a) `FLAT-03-C2` trigger | 7 | **subsumed** by D2-C — no text change |
| 9 | D5 | already committed (`e2ecd4b`) | ratification only (Requirement 3) |

Items 1–5 are independent of the D2 decision and can land immediately. Items 6–8 cannot.

### Effort estimate

| Item | Estimate |
|---|---|
| D1 | ~20 min (two text edits + one label) |
| D2 Option A **(not adopted — Amendment 2026-09-23, iter 2)** | ~1 day (policy change, post-recovery re-evaluation, two probe tests promoted to real tests, corpus re-run) |
| D2 Option B **(not adopted — Amendment 2026-09-23, iter 2)** | ~1 hour (HR5 rewording, two contract edits) + an unscoped decision on the flat/tree asymmetry |
| **D2 Option C (ADOPTED)** **(Amendment 2026-09-23)** | ~1 day (Option A) → ~~**~1.5–2 days**~~ **~2.5–3 days, including the Wave 3 corpus re-run (Amendment 2026-09-23, iter 2)**. Rationale: Option A's ~1 day (override, probe tests re-derived and promoted, corpus re-run) + ~0.5–1 day for the quarantine storage helper, the `_erase_quarantine` cascade step, the ERASE test, and the `ARCHITECTURE.md` layout row. **(iter 2)** + ~1 day for the `sha256` keying and operator `erase_quarantine(sha256)` path, the lifecycle TTL operator step, clear-on-success, the `quarantine/` prefix guard, `QUARANTINE_WRITES_TOTAL` + `quarantine_write` registration, and the extra probes (route guard, raster-to-FLAT not rejected, static no-reader test). **(Amendment 2026-09-23, iter 3):** **~2.75–3.25 days**. That is +0.25 day for sha256 surfacing on the rejected job (Task 7.5d: worker hash, status-body test, `FLAT-04-C2` note) and the tree and flat write-failure tests. |
| D3, D4(a-c) | ~30 min total |
| D5 | 0 — applied (committed `e2ecd4b`) |
| D6 | ~5 min |

## Test Strategy

- **D1** — no new test. Label `tests/test_helpers_combined.py:420::test_ensure_tessdata_non_latin_raises_latin_degrades` with `LANG-01-C2` once the contract text is amended. Both halves (non-Latin raises, Latin degrades to `['deu','eng']`, pre-baked returns unchanged) are already asserted.
- **D2 Option A** **(not adopted — Amendment 2026-09-23, iter 2)** — **(Amendment 2026-09-23:** the probe tests are not in VCS and must be re-derived first**)** promote the two probe tests to permanent tests and label them `OCR-01-C3` / `FLAT-03-C2`. They currently fail; under Option A they pass, which is the acceptance criterion. Add a corpus re-run comparing verdict counts against [[rfc047-d9-final-baseline]].
- **D2 Option B** **(not adopted — Amendment 2026-09-23, iter 2)** — the probe tests are **discarded**, and a test asserting persist-with-FAIL on the tree route is written and labelled `OCR-01-C3` instead. Note that this makes the gate green on a contract whose earlier text was refuted, which is precisely why the amendment must land first.
- **D2 Option C (ADOPTED) (Amendment 2026-09-23)** — the probe tests are **not in VCS**; re-derive them, written literally to the `OCR-01-C3` / `FLAT-03-C2` contract text with no reference to the implementation, and label them only once they pass (Requirement 1 AC4). Cover all three `OCR-01-C3` triggers (still-garbled after retry, escalation disabled, exception during retry) plus a `NODE_GARBLING` case; each asserts `LowQualityTreeError`, `save_doc` not called, `LOW_QUALITY_TREES` incremented, and `quarantine/<doc_id>.json` written before the raise (Requirement 4 AC1–2). Add a flat-guard quarantine assertion. Add an `ERASE-01` test that `delete_doc` removes `quarantine/<doc_id>.json` + `.meta.json` and is idempotent when absent (Requirement 4 AC4). Add a check that no MCP query tool reads `quarantine/` (AC3). Corpus re-run vs [[rfc047-d9-final-baseline]] via the corpus-score-diff workflow. **(Amendment 2026-09-23, iter 2):** all `quarantine/<doc_id>` assertions above read `quarantine/<sha256>`. Add: a probe that a raster-recovered-to-FLAT document (`ok=False`, `route=FLAT`, `first_defect=GARBLING`) is **not** rejected by the tree override; a flat-route test that the quarantine write happens inside `_persist_flat_result` before `return None`; clear-on-success (a later successful persist of the same bytes deletes `quarantine/<sha256>.*`); `erase_quarantine(sha256)` idempotency; a `TestHR2CascadeStoreCoverage` assertion for `quarantine/`; and the AC3 static check is **mandatory** — a test asserting no `src/` read path references `"quarantine/"` except the storage writer/eraser helpers. The lifecycle TTL is an operator/infra rule and is verified by inspection (`mc ilm rule ls`), not by the suite. **(Amendment 2026-09-23, iter 3):** add the following:
  - **Write-failure tests (R4 AC7):** on both routes, a raising `put_object` still produces `LowQualityTreeError`, with no `save_doc` / `save_flat_doc`, `QUARANTINE_WRITES_TOTAL{result="error"}` +1, and `quarantine_write`=`failed`.
  - **`gate_result is None` test:** the override still rejects, without a `TypeError`.
  - **`filenames[]` merge test.**
  - **sha256 surfacing tests (R4 AC8):** the job hash and the status body carry the staged file's SHA-256, and the arq return value stays `""`.
  - **Existing erasure tests that must be updated with the new step:**
    - `tests/test_storage.py:701-759` (exact `expected_names`);
    - `tests/test_storage.py:762-794` (exact required-flags dict, `"quarantine": False`);
    - `tests/test_integration.py:149-194` (`len == 11` → `12`, with `partial_purge` still `False`; its fixture's `.meta.json` stub supplies a sha256);
    - `tests/test_client.py:806-863` (`TestValidateErasureManifest`);
    - `tests/test_registry.py:605-640` (`TestHR2CascadeStoreCoverage`).
- **D3** — no new test; `test_flat_01_c3_roles_are_typed_and_gate_independent` already asserts the five-role set.
- **D4** — no new tests; `tests/test_flat.py:1529`, `tests/test_converters.py:1270` and `tests/test_converters.py:783` already carry the labels.
- **D5** — `test_erase_01_c2_prefix_loops_tolerate_nosuchkey_but_surface_other_errors` is written and ~~in the working tree~~ committed in `e2ecd4b` at `tests/test_storage.py:201` **(Amendment 2026-09-23)**.
- **D6** — verify by creating a `.pyc` for a deleted contract test and confirming the gate reports FAIL.

Per `CLAUDE.md`, any suite run uses `make test` (or `make test PYTEST_ARGS=...`) — never detached, never unbounded.

## Risks

1. **D2 Option A changes verdict outcomes for documents not in the corpus.** Blast radius is measured at zero *on the 25-doc corpus as of 2026-09-22*. A document that garbles unrecoverably in future would fail its job rather than store an inspectable FAIL artifact. Mitigation: attach the diagnostic payload to the raised error. **(Amendment 2026-09-23):** the adopted Option C carries the same verdict change; the error-payload mitigation is infeasible across the worker subprocess boundary and is replaced by quarantine — the rejected tree, verdict and decision trail are written to `quarantine/<doc_id>.json` before the raise, so the document stays inspectable. ~~Mitigated.~~ **(Amendment 2026-09-23, iter 2):** **Partially mitigated.** As first written the mitigation could not work: no `doc_id` exists at either reject site, and there is no decision trail to write. It now rests on `sha256` keying (`quarantine/<sha256>.json` + `.meta.json`, tree + verdict + garble samples), on the flat write being placed where it actually runs (inside `_persist_flat_result`), and on an erasure path that works for never-persisted documents (`_erase_quarantine` via `ctx.sha256`, plus operator `erase_quarantine(sha256)`). Residual: inspectability is bounded by the 30-day TTL, and a quarantine write failure leaves only the arq error (by design — HR5 wins).
2. **D2 Option B erodes a hard rule.** Once HR5 reads "persist but record", there is no static check of it, and the next agent reading `CLAUDE.md` inherits a rule with unstated exceptions. Mitigation: if B is chosen, state the flat/tree asymmetry and its reason in HR5 itself, not in an RFC.
3. **Labelling ahead of amending.** The single largest process risk: an agent clearing the two remaining FAILs by adding `# LANG-01-C2` / `# OCR-01-C3` comments to tests that pin the *opposite* behaviour. The gate would go green and the drift would become permanently invisible. This RFC's Requirement 1.2 exists to forbid that. **(Amendment 2026-09-23):** the rule bars labels where the contract's **effect** is contradicted; an existing label on a contract whose effect is true and asserted but whose trigger text is stale may stay if the drift is listed in this RFC (`FLAT-03-C2`, via D4(a)) — see the Requirement 1 clarification and AC4.
4. **D6 changes the gate's own count.** Hardening the grep can only *reduce* hits. If any current PASS is bytecode-only it will flip to FAIL. Verified not to be the case today (identical counts with and without bytecode), but the check must be re-run after the change rather than assumed.
5. **The gate remains a grep.** Nothing in this RFC makes the contracts gate verify that a labelled test actually asserts the contract. Both drifted contracts were found by hand, by agents writing probe tests. That detection method does not scale and is not institutionalised here.
6. **(Amendment 2026-09-23) `quarantine/` is a new PII store.** Option C writes extracted text of rejected documents to a new MinIO prefix. Right-to-erasure (HR2) must cover it: if `_erase_quarantine` is missing from the cascade, or `CLAUDE.md` HR2's purge list is not updated, a DSR would leave PII behind in the one store nobody reads. Mitigation: Requirement 4 AC4 + an `ERASE-01` clause and test; the `CLAUDE.md` HR2 edit is tracked as a human-approved task; retention/TTL is Open Question 4. **(Amendment 2026-09-23, iter 2):** the store is keyed by `sha256`, so `delete_doc` reaches it through `ctx.sha256`, and a document with no `doc_id` is erased by operator `erase_quarantine(sha256)`. The HR2 import-time guard (`register_storage_prefix("quarantine/")` + `_PREFIX_TO_ERASURE_STEPS` + `_ERASURE_MANIFEST`) makes a missing cascade step an `ImportError`, not a silent gap. Retention is bounded by a 30-day lifecycle TTL and clear-on-success. Backups and object-store snapshots capture `quarantine/` like any other prefix, so the existing manual backup purge covers it. **(Amendment 2026-09-23, iter 3) — correction:** the previous sentence is withdrawn. Whether any documented backup exists, and whether its purge covers `quarantine/`, is **unverified**. Task 6.2 checks for a documented backup and records the result. Task 7.5c checks bucket versioning (`mc version info`) and, if versioning is on, adds a noncurrent-version expiry rule for `quarantine/`. With versioning on, a `remove_object`-based erasure leaves noncurrent versions behind in every prefix. That is a general HR2 exposure, flagged here and out of this RFC's scope. Residual risks: the lifecycle rule is an operator step (owner: Salil Trehan) and is not enforced by code; if both the sidecar and the registry row lack a `sha256`, `_erase_quarantine` can do nothing and the operator path is the only route.

## Open Questions

1. **Should HR5 bind the tree route (Option A) or be amended to the persist-with-FAIL position (Option B)?** This is D2. It is the reason this RFC is a proposal and not a patch. It requires explicit human approval and must not be resolved by an agent. Until it is answered, `OCR-01-C3` stays FAIL. **RESOLVED (Amendment 2026-09-23)** — the user chose **Option C** (reject + quarantine) on 2026-09-23; neither A nor B as posed. `OCR-01-C3` stays FAIL until D2-C lands.
2. **Should the flat/tree asymmetry be reconciled independently of D2?** RESOLVED — no. Under Option A it disappears; under Option B it must be addressed as part of B. Reconciling it separately would mean choosing D2's answer by implication.
3. **Should the contracts gate verify assertion content rather than string presence?** RESOLVED — out of scope here (Non-Goal 4). Recorded as Risk 5 for a successor.
4. ~~**(Amendment 2026-09-23) What retention/TTL applies to `quarantine/`, and does it require HR3's ZDR routing considerations?**~~ **(Amendment 2026-09-23, iter 2) What storage-limitation (retention) policy applies to `quarantine/`?** `quarantine/` holds extracted text of PII-bearing documents that were rejected rather than served. ~~Open: whether objects expire (TTL / lifecycle rule) or persist until explicit erasure, and whether its existence changes the HR3 no-training / zero-retention / EU-residency analysis for the corpus. OPEN — human decision.~~ **RESOLVED (iter 2, user decision):** a 30-day MinIO lifecycle TTL on `quarantine/`, plus idempotent deletion of `quarantine/<sha256>.*` when the same bytes later persist successfully. This is a storage-limitation question, not an HR3 one: the quarantine write adds no LLM egress, and the objects sit in the same bucket and region as `processed/`. Remaining open item: who owns the lifecycle rule (operator/infra), since no lifecycle configuration exists in the repo today. **(Amendment 2026-09-23, iter 3):** that item is still **open**. Clear-on-success deleting the copy once the same bytes persist is intended.
5. **(Amendment 2026-09-23, iter 3) Is there a documented backup of the MinIO bucket, and is bucket versioning on?** OPEN, to be checked in Tasks 6.2 and 7.5c. Until answered, this RFC makes no claim that backups of `quarantine/` are purged.

## Consequences

- Five of the six items are contract-text or one-line changes with no behavioural effect. The RFC's weight is entirely in D2.
- After items 1–5 land, the contracts gate reads **PASS=65 FAIL=1**, with the single remaining FAIL (`OCR-01-C3`) standing as the visible marker of an unanswered hard-rule question. That is the correct state for it to be in, and it should not be cleared by labelling. **(Amendment 2026-09-23):** after D2-C lands and the re-derived `OCR-01-C3` probe test passes and is labelled, the gate reads **FAIL=0**; PASS rises further by the two new clauses (`ERASE-01` quarantine erasure, `OCR-01` quarantine-before-reject) once their tests are labelled. (Counts remain on the gate's line-count basis — see the Context amendment.)
- `LANG-01-C2` and `CONV-01-C5` stop contradicting `ocr_langs.py`, and the D6/ISS-34 rationale — that gibberish is worse than less text — becomes readable from the contract rather than only from a docstring.
- HR2's erasure cascade can reach a clean `errors == []` on retry (D5).
- The contracts gate becomes non-maskable by build artefacts (D6).
- Whichever way D2 resolves, one of `CLAUDE.md` Hard Rule 5 or `helpers/gates.py`'s `REASON_POLICY` changes. They cannot both stay as they are. **(Amendment 2026-09-23):** under the adopted Option C, **neither** changes — HR5 stays as written and `REASON_POLICY` / `decide_route` are untouched; the post-recovery override in `index()` makes the code conform to HR5. What changes instead: a new `quarantine/` MinIO prefix, an HR2 cascade step (`_erase_quarantine`), two new contract clauses, an `ARCHITECTURE.md` layout row, and (pending human approval) `CLAUDE.md` HR2's purge list. **(Amendment 2026-09-23, iter 2):** correction — `CLAUDE.md` HR5 does get a one-clause amendment (unserved, erasable `quarantine/` copy permitted), bundled with the HR2 edit under human-approved Task 7.9. `REASON_POLICY` / `decide_route` remain untouched. Also new: a `sha256`-keyed layout, a 30-day lifecycle TTL, clear-on-success, the `quarantine/` prefix-guard registration, an operator `erase_quarantine(sha256)` helper, `QUARANTINE_WRITES_TOTAL`, and the `quarantine_write` decision point.
- **(Amendment 2026-09-23, iter 2) Gate counts, stated precisely.** The gate prints one line per contract ID (61) plus 5 module-coverage lines = **66 gate lines**. Today: PASS=64 FAIL=2. Checkpoint A: **PASS=65 FAIL=1**. Checkpoint B: **PASS=68 FAIL=0** (66 existing lines + the 2 new IDs `OCR-01-C4`, `ERASE-01-C4`).

## Traceability

| Artifact | Reference |
|----------|-----------|
| Design | ~~Not yet generated — to be built via `rfc-artifact-build` once D2 is settled~~ [[design-rfc049-contract-drift-remediation]] **(Amendment 2026-09-23)** |
| Tasks | ~~Not yet generated — see above~~ [[tasks-rfc049-contract-drift-remediation]] **(Amendment 2026-09-23)** |
| Supersedes | N/A |
| Amends | `agents/contracts/lang-01.yaml` (LANG-01-C2), `agents/contracts/ocr-01.yaml` (OCR-01-C3), `agents/contracts/flat-01.yaml` (FLAT-01-C3), `agents/contracts/flat-03.yaml` (FLAT-03-C2), `agents/contracts/index-01.yaml` (INDEX-01-C2), `agents/contracts/conv-01.yaml` (CONV-01-C5); **(Amendment 2026-09-23)** `agents/contracts/erase-01.yaml` (new quarantine-erasure clause), `agents/contracts/ocr-01.yaml` (new quarantine-before-reject clause), `ARCHITECTURE.md` (MinIO layout row), `CLAUDE.md` HR2 purge list (pending human approval); **(Amendment 2026-09-23, iter 2)** `CLAUDE.md` HR5 one-clause amendment (pending human approval, bundled with HR2 in Task 7.9), `ARCHITECTURE.md` Compliance "Required erasure fan-out" block + lifecycle operator step, `agents/contracts/erase-01.yaml` header cascade list; **(Amendment 2026-09-23, iter 3)** `agents/contracts/flat-04.yaml` (`FLAT-04-C2` editorial: sha256 in the job hash), `src/pageindex_mcp/worker/job.py` (sha256 on `low_quality_tree`), `scripts/erase-quarantine.sh` (new), `DESIGN.md` Upload & Job-Status API |
| Hard rule at issue | `CLAUDE.md` Hard Rule 5 (D2); Hard Rule 2 (D5; **(Amendment 2026-09-23)** and D2-C quarantine erasure) |
| Prior art | [[RFC-047]] (gate-layer correctness), [[RFC-044]] (routing authority), [[RFC-030]] (persist-with-FAIL position), [[RFC-043]] (erasure hardening), [[RFC-041]] (lifecycle lint) |
| Evidence | [[rfc047-d9-final-baseline]] (0 FAIL / 25 docs, 2026-09-22 — D2 blast radius); `scripts/gates/contracts.sh` output PASS=64 FAIL=2 |
| Memory | ~~[[contracts-gate-greps-bytecode]] (D6), [[test-suite-reduction-2026-09]]~~ **(Amendment 2026-09-23: both wikilinks resolved nowhere)** D6 bytecode shape now inlined in D6; git `91e7249` *"test: consolidate the suite from 2510 collected to 923"* (why label drift was the expected diagnosis); D5 fix git `e2ecd4b` |

## Appendix A: D2 Options A and B (verbatim, moved 2026-09-23, iter 3)

**(Amendment 2026-09-23, iter 3):** these sections were moved here verbatim from [D2](#d2-ocr-01-c3-vs-hard-rule-5--the-decision-requires-explicit-approval) so that the decision record reads around the adopted [Option C](#option-c--reject-and-quarantine-adopted-2026-09-23). Nothing was deleted. The iter-1 and iter-2 amendment markers inside them are preserved as they were.

### A.1 Options A and B

#### Option A — restore HR5 as written

Route unrecovered `GARBLING` on the tree route to `Route.REJECT`, so `index()` raises `LowQualityTreeError('garbling')` and nothing is persisted. Mechanically this is a `REASON_POLICY` / `decide_route` change plus a post-recovery re-evaluation, since `RETRY_OCR → TREE` is load-bearing *during* the retry and must only become `REJECT` once recovery is exhausted.

- **Restores** HR5 as a bright line, and removes the flat/tree asymmetry without touching the flat side.
- **Makes `OCR-01-C3` and `FLAT-03-C2` true as written** (both currently refuted).
- **Cost:** documents that today complete with a FAIL verdict would fail the arq job instead.
- **Blast radius, measured:** the RFC-047 D9 final corpus baseline ([[rfc047-d9-final-baseline]], 2026-09-22, 25 documents) records **0 FAIL** — *"Remaining FAIL: 0 — all previous FAILs are now PASS."* The single REJECTED document (#12, image pie chart) is *already* rejected for garbling, via the image/flat path, and the baseline calls that *"correct gate behavior"*. **On the current corpus, Option A's blast radius is zero documents.** This is the strongest single piece of evidence in this RFC, and it cuts toward A.
- **Residual risk:** zero-today is not zero-forever. A future Arabic or scanned document that garbles unrecoverably would go from a stored FAIL artifact (inspectable) to a failed job (not inspectable). Mitigating that is a matter of preserving the diagnostic payload on the error, not of persisting the tree. **(Amendment 2026-09-23):** attaching the payload to the error was found infeasible cheaply — see Option C; quarantine replaces this mitigation.

#### Option B — amend HR5 and `OCR-01-C3` to the RFC-030 position

Ratify persist-with-FAIL-verdict: argue that a tree stored *with a recorded FAIL verdict and a `persistence_route_dispatch` decision event naming `persist_tree_with_fail_verdict`* is not "silently" persisted, which is what HR5 actually prohibits.

- **Zero code change in its minimal form.** **(Amendment 2026-09-23:** was "Zero code change."; its asymmetry sub-options (i)/(ii) below may need code.**)** The corpus is unaffected by construction.
- **Preserves inspectability:** a FAIL-verdict artifact can be read, scored and diffed; a failed job cannot.
- **Consistent with RFC-030's direction**, which introduced this position.
- **Cost:** HR5 stops being a bright line. "Never persist" becomes "persist, but record" — a rule that requires reading the verdict field to evaluate, and which cannot be checked by a static gate. Every future reader of HR5 must now also know which routes it does and does not bind.
- **Cost:** the flat/tree asymmetry **remains** unless reconciled in the same change. Option B is not complete without either (i) making the flat route also persist-with-FAIL, which weakens the one place HR5 still holds, or (ii) documenting the asymmetry as intentional with a stated reason. Neither sub-option has been costed here.
- **Cost:** `FLAT-03-C2` would also need amending, and `LOW_QUALITY_TREES{reason=garbling}` would stop being a reliable count of garbling rejections.

### A.2 Original sequencing table (verbatim, moved 2026-09-23, iter 3)

| Order | Item | Blocked by | Nature |
|---|---|---|---|
| 1 | D6 gate hardening | — | one line, `scripts/gates/contracts.sh` |
| 2 | D3 `FLAT-01-C3` role set | — | contract text only |
| 3 | D4(b) `INDEX-01-C2` trigger | — | contract text only |
| 4 | D1 `LANG-01-C2` + header comment | — | contract text; then label the existing test |
| 5 | D4(c) `CONV-01-C5` effect | D1 (shared wording) | contract text only |
| 6 | **D2 decision** | **human approval** | — |
| 7 | D2 execution (A: code + `REASON_POLICY`; B: `CLAUDE.md` + contract text) **(not adopted — Amendment 2026-09-23, iter 2)** | D2 decision | see D2 |
| 8 | D4(a) `FLAT-03-C2` trigger | D2 outcome (subsumed under A) | contract text only |
| 9 | D5 | already applied | ratification only |

### A.3 Iter-2 current-state table (verbatim, moved 2026-09-23, iter 3)

**Current state (2026-09-23) (Amendment 2026-09-23, iter 2)** — read this first; the per-item text below keeps its amendment trail.

| Item | State |
|---|---|
| [D1](#d1-lang-01-c2-is-stale--the-raise-is-the-fix-not-the-bug) | Amend `LANG-01-C2` (+ `lang-01.yaml` header) by script class, then label the existing test. |
| [D2](#d2-ocr-01-c3-vs-hard-rule-5--the-decision-requires-explicit-approval) | **[Option C](#option-c--reject-and-quarantine-adopted-2026-09-23) adopted.** Post-recovery tree override guarded by `route == TREE`; flat quarantine written inside `_persist_flat_result`; quarantine keyed by `sha256`; 30-day lifecycle TTL + clear-on-success; `_erase_quarantine` cascade step + `quarantine/` prefix guard + operator `erase_quarantine(sha256)`; new `OCR-01-C4` / `ERASE-01-C4` clauses; `CLAUDE.md` HR2 + HR5 edits **pending human approval** (Task 7.9). |
| [D3](#d3-flat-01-c3s-role-set-is-incomplete--add-image), [D4](#d4-three-drifted-triggereffect-clauses--substance-intact-text-stale) | Editorial. D4(a) subsumed by D2-C; D4(b), D4(c) text edits. |
| [D5](#d5-erase-01-c2-idempotency-hole--ratification-fix-already-applied) | Ratified; committed `e2ecd4b`. |
| [D6](#d6-the-contracts-gates-own-grep-is-unhardened--latent-one-line-fix) | One-line grep hardening. |
