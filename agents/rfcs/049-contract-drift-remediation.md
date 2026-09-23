<!-- Space: CITRA -->
<!-- Title: RFC-049: Contract Drift Remediation -->
<!-- Folder: RFCs -->

---
id: "RFC-049"
title: "Contract Drift Remediation"
type: rfc
status: draft
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
governs: []
supersedes: []
---

## Context

On 2026-09-23 the contracts gate (`scripts/gates/contracts.sh`) stood at **PASS=44 FAIL=22**. The gate is a grep: for every `id:` in `agents/contracts/*.yaml` it searches `tests/` for a literal occurrence of the contract ID. A FAIL therefore means only "no test mentions this ID" — it says nothing about whether the behaviour is covered.

The remediation triaged all 22. The expectation going in was that most were **label drift**: a test consolidation (see [[test-suite-reduction-2026-09]], 2510 → 923 tests) had dropped the ID comments while keeping the assertions. That expectation was wrong. The failures were overwhelmingly **missing tests** — the behaviour genuinely had no assertion anywhere — and in two cases something worse: **the contract had drifted away from the code**.

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

1. Bring `LANG-01-C2` and `CONV-01-C5` into agreement with a deliberate, documented, test-pinned behaviour in `ocr_langs.py`, so the two contracts stop asserting the opposite of the code.
2. Put the `OCR-01-C3` / Hard Rule 5 contradiction in front of a human with both resolutions costed, and take neither unilaterally.
3. Close three editorial drifts (`FLAT-01-C3`, `FLAT-03-C2`, `INDEX-01-C2`) where the contract's substance is right and only its text is stale.
4. Record and ratify the `ERASE-01-C2` idempotency defect and its fix.
5. Harden the contracts gate's own grep so a stale `.pyc` can never mask a deleted contract test.

## Non-Goals

1. **No new contracts.** This RFC amends and ratifies existing contract text; it does not add coverage for behaviour that has no contract today.
2. **No change to `validate_tree`.** Its byte-for-byte stability is an [[RFC-004]] Amendment-1 invariant and neither D2 option touches it.
3. **No re-litigation of the RFC-044 authority model.** D2 Option A changes one `REASON_POLICY` entry; it does not reopen who owns routing.
4. **No contracts-gate redesign.** D6 is a one-line hardening. The structural weakness — that the gate greps for a string rather than verifying an assertion — is real and is *not* addressed here.
5. **No corpus re-run.** D2 Option A would warrant one; that belongs to whichever option is approved, not to this RFC.

## Glossary

| Term | Definition |
|------|------------|
| Contract drift | A contract whose stated `trigger`/`effect` no longer describes production behaviour. Distinct from a missing test, and strictly more dangerous, because a labelled test makes it report green. |
| Editorial drift | Drift in which the contract's *intent* is still correct and only its prose names the wrong function, line, or value set. No code change follows. |
| Probe test | A test written deliberately to the literal contract text, with no reference to the implementation, used to detect drift. Two of the six items below were found this way. |
| Label drift | A test that covers a contract but no longer contains its ID, so the grep-based gate cannot see it. The expected diagnosis for the 22 FAILs; it turned out to be the minority. |

## Requirements

### Requirement 1: No contract may report green against code that contradicts it

**User Story:** As an engineer reading a contract to learn the system's guarantees, I want the contract to describe what the code actually does, so that I do not build on a guarantee that was withdrawn two RFCs ago.

#### Acceptance Criteria

1. WHEN a contract's `effect` text is contradicted by a deliberate, documented code path, THE contract SHALL be amended or the code SHALL be changed — a labelled test that pins the code while the contract says otherwise is not an acceptable resting state.
2. WHILE such a contradiction is unresolved, THE contract's gate entry SHALL be left at FAIL rather than labelled, so the gate's red reflects the open question.
3. WHERE a hard rule in `CLAUDE.md` is the thing contradicted, THE resolution SHALL require explicit human approval and SHALL NOT be taken by an agent.

### Requirement 2: The contracts gate must not be maskable by build artefacts

**User Story:** As a reviewer trusting the gate's PASS count, I want that count to be derived only from source, so that deleting a contract test cannot leave the gate green.

#### Acceptance Criteria

1. WHEN the contracts gate searches `tests/` for a contract ID, THE search SHALL consider only `*.py` source files.
2. THE gate SHALL exclude `__pycache__` directories from the search.

## Decision Summary

Six items. **D2 requires explicit human approval and is presented as two options, unresolved.** D5 is a ratification of a fix already in the working tree. D3, D4a, D4b and D4c are editorial. D1 and D6 are small and, in the author's view, uncontroversial — but they are still proposals.

### D1: `LANG-01-C2` is stale — the raise is the fix, not the bug

**Contract:** `agents/contracts/lang-01.yaml`
**Code:** `src/pageindex_mcp/converters/ocr_langs.py`

**The drift.** `LANG-01-C2`'s effect says a missing language "is dropped from the returned set rather than raising", and the file's header comment reinforces it: *"ensure_tessdata NEVER fails hard"*. The code raises `TessdataUnavailableError` at **three** sites:

| Site | Function | Condition |
|---|---|---|
| `ocr_langs.py:299` | `_ensure_lang_no_prefix` | non-Latin lang, cached probe says absent |
| `ocr_langs.py:331` | `_ensure_lang_no_prefix` | non-Latin lang, live probe says absent |
| `ocr_langs.py:368` | `_ensure_lang_with_prefix` | `lang not in _LATIN_LANGS` and not provisionable |
| `ocr_langs.py:399` | `_ensure_tessdata_empty_fallback` | nothing survived and a non-Latin lang was requested — *"refusing Latin-only fallback"* |

(Four raise statements across three functions; the two in `_ensure_lang_no_prefix` are the cache-miss and probe-miss branches of the same guard.)

This is **deliberate and documented**. The `ensure_tessdata` docstring states it under D6/ISS-34: *"a missing non-Latin-script language raises `TessdataUnavailableError` instead of being silently dropped, since that would silently degrade OCR to gibberish/empty output for scripts Latin OCR cannot read."* Degrading Arabic to `deu,eng` does not produce less text — it produces confidently wrong text, which is worse, and which the garble detectors then have to catch downstream. The raise *is* the fix.

**Proposed amended effect.** Split the guarantee by script class:

> For each missing language, `_try_download_tessdata` attempts a fetch only when `TESSDATA_ALLOW_DOWNLOAD` permits network egress. A missing **Latin-script** language (a member of `_LATIN_LANGS`) is dropped from the returned set with a logged degradation, and the returned list falls back to at least `['deu','eng']` rather than being empty. A missing **non-Latin-script** language raises `TessdataUnavailableError` rather than degrading, because Latin-only OCR on a non-Latin script yields gibberish rather than less text (D6/ISS-34); each caller that can proceed without that script catches the error and degrades explicitly at its own call site.

**Note on the catch sites.** The triage report named "the indexer image branch" as the catcher. There are **five**, and this correction matters for the amended boundary text:

- `client/indexer.py:1262` — standalone-image branch; degrades to `['deu','eng']` and emits a `standalone_image_tessdata_availability` decision event.
- `client/indexer.py:1317` — the RFC-046 D4 bounded detect-correct-retry; degrades by *keeping the original* `img_langs`, not to `deu,eng`.
- `client/images.py:137` — degrades to `['deu','eng']` with a warning.
- `converters/pictures.py:1644`.
- `client/recovery.py:436`.

Uncaught, the error is terminal by design: `worker/errors.py:30` classifies `TessdataUnavailableError` as `converter_env_missing`, `terminal=True`.

**Test.** Both halves are already discharged by `tests/test_helpers_combined.py:420::test_ensure_tessdata_non_latin_raises_latin_degrades`, which asserts `pytest.raises(TessdataUnavailableError, match="ara")`, `ensure_tessdata(["fra"]) == ["deu","eng"]`, and the pre-baked no-op. **Once this amendment lands, the `LANG-01-C2` label should be placed on that test** and the gate's first remaining FAIL clears. Labelling it *before* the amendment would be exactly the failure mode Requirement 1 forbids.

**Also amend the file header comment** ("ensure_tessdata NEVER fails hard") in the same edit; it is the same claim in a second place.

### D2: `OCR-01-C3` vs Hard Rule 5 — THE DECISION. Requires explicit approval.

**Status: OPEN. Both options are presented. This item must not be actioned by an agent.**

**Contract:** `agents/contracts/ocr-01.yaml` (`OCR-01-C3`)
**Hard Rule:** `CLAUDE.md` #5 — *"Never silently persist a low-quality tree. `validate_tree()` must run before `save_doc`; a failing tree must surface as an arq `low_quality_tree` error, not a stored artifact."*

#### What the code does

`REASON_POLICY[TreeDefect.GARBLING]` is `_ReasonPolicy.RETRY_OCR` (`helpers/gates.py:581-589`). `decide_route` maps `RETRY_OCR → Route.TREE` unconditionally (`helpers/types.py:369-370`, comment: *"retry handled upstream"*). So an unrecovered garbling on the tree route arrives at the route dispatch in `index()` as `(ok=False, route=Route.TREE)` and hits this case (`client/indexer.py:2557-2563`):

```python
case (False, Route.TREE) | (False, Route.PERSIST_FAIL):
    logger.warning(
        "Persisting low-quality tree with FAIL verdict for %s: reason=%s",
        filename, state.reason,
    )
    # fall through to _persist_tree_result below
```

`_persist_tree_result` calls `save_doc`. `LowQualityTreeError('garbling')` is never raised on this path.

The guarantee survives **only on the flat route**, via a pre-match guard at `client/indexer.py:2488`:

```python
if state.flat_garble_unrecovered:
    LOW_QUALITY_TREES.labels(reason="garbling").inc()
    raise LowQualityTreeError("garbling")
```

`state.flat_garble_unrecovered` is set at `indexer.py:1644` inside `_persist_flat_result`, from `_garble_check_flat_blocks`, and short-circuits that function at `:1745` (`return None`) before enrichment. So the same defect — unrecovered garbling — rejects on the flat route and persists with a FAIL verdict on the tree route. That asymmetry is the substance of this item.

**One correction to the triage report:** `persist_tree_with_fail_verdict` is not a function. It is the `choice` string of the `persistence_route_dispatch` decision event (`indexer.py:2500-2501`, declared in `obs/decision_points.py:1006`). The actual call is the fall-through to `_persist_tree_result`.

#### Evidence, stated as evidence

- An agent wrote a probe test **literally to `OCR-01-C3`** — no reference to the implementation. It failed on **all three** of the contract's stated triggers (still-garbled after retry; `OCR_ESCALATION` disabled; exception during the retry), with: *"index() returned instead of rejecting; save_doc was called on a rejected tree."*
- **Independently**, a second agent wrote a probe test literally to `FLAT-03-C2`. It failed with `DID NOT RAISE`.
- Two agents, two entry points, one conclusion. This is two probe tests, not a corpus measurement — but it is not a single agent's single observation either.

#### Option A — restore HR5 as written

Route unrecovered `GARBLING` on the tree route to `Route.REJECT`, so `index()` raises `LowQualityTreeError('garbling')` and nothing is persisted. Mechanically this is a `REASON_POLICY` / `decide_route` change plus a post-recovery re-evaluation, since `RETRY_OCR → TREE` is load-bearing *during* the retry and must only become `REJECT` once recovery is exhausted.

- **Restores** HR5 as a bright line, and removes the flat/tree asymmetry without touching the flat side.
- **Makes `OCR-01-C3` and `FLAT-03-C2` true as written** (both currently refuted).
- **Cost:** documents that today complete with a FAIL verdict would fail the arq job instead.
- **Blast radius, measured:** the RFC-047 D9 final corpus baseline ([[rfc047-d9-final-baseline]], 2026-09-22, 25 documents) records **0 FAIL** — *"Remaining FAIL: 0 — all previous FAILs are now PASS."* The single REJECTED document (#12, image pie chart) is *already* rejected for garbling, via the image/flat path, and the baseline calls that *"correct gate behavior"*. **On the current corpus, Option A's blast radius is zero documents.** This is the strongest single piece of evidence in this RFC, and it cuts toward A.
- **Residual risk:** zero-today is not zero-forever. A future Arabic or scanned document that garbles unrecoverably would go from a stored FAIL artifact (inspectable) to a failed job (not inspectable). Mitigating that is a matter of preserving the diagnostic payload on the error, not of persisting the tree.

#### Option B — amend HR5 and `OCR-01-C3` to the RFC-030 position

Ratify persist-with-FAIL-verdict: argue that a tree stored *with a recorded FAIL verdict and a `persistence_route_dispatch` decision event naming `persist_tree_with_fail_verdict`* is not "silently" persisted, which is what HR5 actually prohibits.

- **Zero code change.** The corpus is unaffected by construction.
- **Preserves inspectability:** a FAIL-verdict artifact can be read, scored and diffed; a failed job cannot.
- **Consistent with RFC-030's direction**, which introduced this position.
- **Cost:** HR5 stops being a bright line. "Never persist" becomes "persist, but record" — a rule that requires reading the verdict field to evaluate, and which cannot be checked by a static gate. Every future reader of HR5 must now also know which routes it does and does not bind.
- **Cost:** the flat/tree asymmetry **remains** unless reconciled in the same change. Option B is not complete without either (i) making the flat route also persist-with-FAIL, which weakens the one place HR5 still holds, or (ii) documenting the asymmetry as intentional with a stated reason. Neither sub-option has been costed here.
- **Cost:** `FLAT-03-C2` would also need amending, and `LOW_QUALITY_TREES{reason=garbling}` would stop being a reliable count of garbling rejections.

#### Recommendation (a recommendation, not a decision)

**Option A**, on three grounds: the measured blast radius is zero documents; it is the only option that leaves `OCR-01-C3` and `FLAT-03-C2` true as written; and it is the only one that removes the route asymmetry rather than documenting it. Option B's inspectability argument is real, and can be recovered under A by attaching the tree's diagnostic payload to the raised error — but that is an addition to A, not a reason to prefer B.

**This is explicitly not settled here.** Hard Rule 5 is a `CLAUDE.md` hard rule; amending it, or changing the code so it becomes true again, is a human decision. Until it is taken, `OCR-01-C3` stays at FAIL in the contracts gate.

### D3: `FLAT-01-C3`'s role set is incomplete — add `image`

**Contract:** `agents/contracts/flat-01.yaml` (`FLAT-01-C3`)
**Code:** `src/pageindex_mcp/helpers/flat.py`

`FLAT-01-C3` states every block carries a role in `{title, prose, kv, table}`. `route_and_extract_flat` emits a fifth:

- `flat.py:117` — `{"role": "image", "index": fig_index}` from `_FLAT_FIGURE_RE` (`^\[Figure:\s*fig-(\d+)...\]$`), optionally carrying `ocr_text` (from `_FLAT_CHART_TEXT_RE`) and `description`.
- `flat.py:127` — `{"role": "image"}` from `_FLAT_RAW_IMAGE_RE` (`^<!--\s*image\s*-->$`).

`block_text` has a matching `if role == "image":` branch at `flat.py:247`. The full emitted set is exactly `{title, prose, kv, table, image}` — verified by enumerating every `"role"` literal in the module.

**Correction to the triage report:** these are at lines **117 and 127**, not ~534-566. The line numbers given were wrong; the finding was right.

**Fix.** Amend the effect to `{title, prose, kv, table, image}`.

**This is editorial, and it is already known to the test.** `tests/test_helpers_combined.py:524::test_flat_01_c3_roles_are_typed_and_gate_independent` asserts against `allowed = {"title","prose","kv","table","image"}` and its docstring already apologises for the gap: *"(plus the later-added 'image' role, which the contract text predates)"*. The amendment lets that parenthesis be deleted. No code change, no behaviour change.

### D4: Three drifted trigger/effect clauses — substance intact, text stale

All three are **editorial**. None implies a code change.

**(a) `FLAT-03-C2`'s trigger.** It says `validate_tree()` returning `(False, 'garbling')` inside `index()` makes `index()` raise. It does not — see D2. The surviving terminal raise is the post-recovery **flat** garble gate: `_garble_check_flat_blocks` sets `state.flat_garble_unrecovered` (`indexer.py:1644`), and the pre-match guard at `indexer.py:2488` raises `LowQualityTreeError("garbling")` and increments `LOW_QUALITY_TREES{reason=garbling}`.

Reword the trigger to name that path. **The effect clause is correct and is now asserted** by `tests/test_flat.py:1529`. Note the dependency: if D2 resolves to Option A, this trigger becomes true as originally written and D4(a) is subsumed; if D2 resolves to Option B, D4(a) must land. **D4(a) is therefore blocked on D2** and should not be applied independently.

**(b) `INDEX-01-C2`'s trigger.** It says the `_run_page_index` fallback fires when "`pdf_to_markdown` raises an exception". The real trigger is **full failure of the `pdf_markdown_converters()` chain**: the chain is built at `indexer.py:830`, and the legacy fallback sits in the `else` arm at `indexer.py:1192-1205`, which increments `PDF_EXTRACT_FALLBACKS`, emits a `pdf_conversion_outcome` decision with choice `all_converters_failed_legacy_fallback`, and calls `self._run_page_index_retrying(file_path)`. No single converter raising is sufficient; the whole chain must fail. `CONV-01-C1` already words this correctly ("on full-chain failure falls back to legacy page_index") — `INDEX-01-C2` should be brought into line with its sibling. Covered by `tests/test_converters.py:1270`.

**(c) `CONV-01-C5`'s effect.** Two errors:

1. It repeats the D1 claim verbatim — *"(never raises; falls back to `['deu','eng']` if nothing is available)"*. Same amendment as D1 applies: it never raises **for Latin**; for non-Latin it raises and the **caller** degrades. Get these two into one form of words.
2. It names a hardcoded `ensure_tessdata(['ara','deu','eng'])` "superset language set (no pre-existing text layer exists to sample for language detection)". The code does not do this. `indexer.py:1258` calls `detected = detect_ocr_langs(filename)` and then `ensure_tessdata(detected)` — the **filename** is the sample. The degradation to `['deu','eng']` happens in the `except TessdataUnavailableError` handler at `:1262`, not inside `ensure_tessdata`. The parenthetical rationale is also wrong: a language signal *is* sampled, just from the filename rather than a text layer.

Covered by `tests/test_converters.py:783`.

### D5: `ERASE-01-C2` idempotency hole — RATIFICATION (fix already applied)

**Contract:** `agents/contracts/erase-01.yaml` (`ERASE-01-C2`) · **Hard Rule 2** path.
**Code:** `src/pageindex_mcp/storage/documents.py`

**The defect.** Every step of the erasure cascade routes through `_remove_object_idempotent` (`documents.py:339`), which treats `S3Error.code == "NoSuchKey"` as idempotent success — **except two**, the prefix-iterating loops `_erase_uploads` (`:358`) and `_erase_figures` (`:405`). Those called `ctx.mc.remove_object` bare inside a wholesale `except S3Error` that appended to `ctx.errors`. Consequence: after a partial failure, a retry finds the objects still **listed** but already **purged**, so `remove_object` raises `NoSuchKey`, and two already-clean stores are reported as erasure failures. `delete_doc` could never reach a clean `errors == []` — which under HR2 is the only evidence that the cascade completed.

`test_erase_01_c2_idempotent_on_missing_doc` (`tests/test_storage.py:184`) misses it because it stubs `list_objects` to `[]`, so those loops never reach `remove_object` at all.

**Severity, honestly.** Real S3 returns 204 on a missing key, so this most likely only fires on a concurrent-delete race or a non-conforming backend. It is a correctness hole in a HR2 path, not an observed production failure.

**What was actually done** (verified against the working tree at time of writing; both files are modified, uncommitted):

- `src/pageindex_mcp/storage/documents.py` — both loops now wrap `remove_object` in a `try/except S3Error` that re-raises unless `getattr(e, "code", "") != "NoSuchKey"`, and `continue`s past the `removed += 1` / `fig_removed += 1` counter on tolerance, so a tolerated miss is not counted as a removal. Each carries a comment tying the tolerance to `ERASE-01-C2` and to `_remove_object_idempotent`. The enclosing `except S3Error` that populates `ctx.errors` is unchanged, so genuine errors still surface.
- `tests/test_storage.py` — a new `test_erase_01_c2_prefix_loops_tolerate_nosuchkey_but_surface_other_errors` stubs `list_objects` to return a real `uploads/` object and a real `figures/` object, then runs `delete_doc` twice: once with `remove_object` raising `NoSuchKey` (asserts `errors == []`) and once with a different `S3Error` (asserts the `uploads/` failure is still reported). Its docstring states explicitly why the sibling test cannot see this.

This item is recorded for **ratification**, not for scheduling. Nothing further is proposed.

### D6: The contracts gate's own grep is unhardened — latent, one-line fix

**Code:** `scripts/gates/contracts.sh:141`

```bash
GREP_HITS=$( (grep -r "$cid" "$REPO_ROOT/tests/" 2>/dev/null || true) | wc -l | tr -d ' ')
```

No `--include='*.py'`, no `--exclude-dir=__pycache__`. Contract IDs live in test docstrings, and docstrings are compiled into `.pyc` bytecode. `grep -r` reports a binary match as one line, which `wc -l` counts as a hit. A contract test that is **deleted** therefore continues to report PASS for as long as its stale `.pyc` survives — the exact shape recorded in [[contracts-gate-greps-bytecode]].

**Verified NOT currently distorting results.** `tests/__pycache__/` presently holds only `conftest`, `__init__` and `_garble_compat` bytecode; no contract-ID-bearing test module is cached, and the gate returns identical counts (PASS=64 FAIL=2) with and without bytecode present. **This is a latent defect, not an active one**, and it should be described that way. The 22→2 remediation above was not affected by it.

**Fix.** Add `--include='*.py' --exclude-dir=__pycache__` to that one `grep -r`. Requirement 2.

## Implementation Plan

### Sequencing

| Order | Item | Blocked by | Nature |
|---|---|---|---|
| 1 | D6 gate hardening | — | one line, `scripts/gates/contracts.sh` |
| 2 | D3 `FLAT-01-C3` role set | — | contract text only |
| 3 | D4(b) `INDEX-01-C2` trigger | — | contract text only |
| 4 | D1 `LANG-01-C2` + header comment | — | contract text; then label the existing test |
| 5 | D4(c) `CONV-01-C5` effect | D1 (shared wording) | contract text only |
| 6 | **D2 decision** | **human approval** | — |
| 7 | D2 execution (A: code + `REASON_POLICY`; B: `CLAUDE.md` + contract text) | D2 decision | see D2 |
| 8 | D4(a) `FLAT-03-C2` trigger | D2 outcome (subsumed under A) | contract text only |
| 9 | D5 | already applied | ratification only |

Items 1–5 are independent of the D2 decision and can land immediately. Items 6–8 cannot.

### Effort estimate

| Item | Estimate |
|---|---|
| D1 | ~20 min (two text edits + one label) |
| D2 Option A | ~1 day (policy change, post-recovery re-evaluation, two probe tests promoted to real tests, corpus re-run) |
| D2 Option B | ~1 hour (HR5 rewording, two contract edits) + an unscoped decision on the flat/tree asymmetry |
| D3, D4(a-c) | ~30 min total |
| D5 | 0 — applied |
| D6 | ~5 min |

## Test Strategy

- **D1** — no new test. Label `tests/test_helpers_combined.py:420::test_ensure_tessdata_non_latin_raises_latin_degrades` with `LANG-01-C2` once the contract text is amended. Both halves (non-Latin raises, Latin degrades to `['deu','eng']`, pre-baked returns unchanged) are already asserted.
- **D2 Option A** — promote the two probe tests to permanent tests and label them `OCR-01-C3` / `FLAT-03-C2`. They currently fail; under Option A they pass, which is the acceptance criterion. Add a corpus re-run comparing verdict counts against [[rfc047-d9-final-baseline]].
- **D2 Option B** — the probe tests are **discarded**, and a test asserting persist-with-FAIL on the tree route is written and labelled `OCR-01-C3` instead. Note that this makes the gate green on a contract whose earlier text was refuted, which is precisely why the amendment must land first.
- **D3** — no new test; `test_flat_01_c3_roles_are_typed_and_gate_independent` already asserts the five-role set.
- **D4** — no new tests; `tests/test_flat.py:1529`, `tests/test_converters.py:1270` and `tests/test_converters.py:783` already carry the labels.
- **D5** — `test_erase_01_c2_prefix_loops_tolerate_nosuchkey_but_surface_other_errors` is written and in the working tree.
- **D6** — verify by creating a `.pyc` for a deleted contract test and confirming the gate reports FAIL.

Per `CLAUDE.md`, any suite run uses `make test` (or `make test PYTEST_ARGS=...`) — never detached, never unbounded.

## Risks

1. **D2 Option A changes verdict outcomes for documents not in the corpus.** Blast radius is measured at zero *on the 25-doc corpus as of 2026-09-22*. A document that garbles unrecoverably in future would fail its job rather than store an inspectable FAIL artifact. Mitigation: attach the diagnostic payload to the raised error.
2. **D2 Option B erodes a hard rule.** Once HR5 reads "persist but record", there is no static check of it, and the next agent reading `CLAUDE.md` inherits a rule with unstated exceptions. Mitigation: if B is chosen, state the flat/tree asymmetry and its reason in HR5 itself, not in an RFC.
3. **Labelling ahead of amending.** The single largest process risk: an agent clearing the two remaining FAILs by adding `# LANG-01-C2` / `# OCR-01-C3` comments to tests that pin the *opposite* behaviour. The gate would go green and the drift would become permanently invisible. This RFC's Requirement 1.2 exists to forbid that.
4. **D6 changes the gate's own count.** Hardening the grep can only *reduce* hits. If any current PASS is bytecode-only it will flip to FAIL. Verified not to be the case today (identical counts with and without bytecode), but the check must be re-run after the change rather than assumed.
5. **The gate remains a grep.** Nothing in this RFC makes the contracts gate verify that a labelled test actually asserts the contract. Both drifted contracts were found by hand, by agents writing probe tests. That detection method does not scale and is not institutionalised here.

## Open Questions

1. **Should HR5 bind the tree route (Option A) or be amended to the persist-with-FAIL position (Option B)?** This is D2. It is the reason this RFC is a proposal and not a patch. It requires explicit human approval and must not be resolved by an agent. Until it is answered, `OCR-01-C3` stays FAIL.
2. **Should the flat/tree asymmetry be reconciled independently of D2?** RESOLVED — no. Under Option A it disappears; under Option B it must be addressed as part of B. Reconciling it separately would mean choosing D2's answer by implication.
3. **Should the contracts gate verify assertion content rather than string presence?** RESOLVED — out of scope here (Non-Goal 4). Recorded as Risk 5 for a successor.

## Consequences

- Five of the six items are contract-text or one-line changes with no behavioural effect. The RFC's weight is entirely in D2.
- After items 1–5 land, the contracts gate reads **PASS=65 FAIL=1**, with the single remaining FAIL (`OCR-01-C3`) standing as the visible marker of an unanswered hard-rule question. That is the correct state for it to be in, and it should not be cleared by labelling.
- `LANG-01-C2` and `CONV-01-C5` stop contradicting `ocr_langs.py`, and the D6/ISS-34 rationale — that gibberish is worse than less text — becomes readable from the contract rather than only from a docstring.
- HR2's erasure cascade can reach a clean `errors == []` on retry (D5).
- The contracts gate becomes non-maskable by build artefacts (D6).
- Whichever way D2 resolves, one of `CLAUDE.md` Hard Rule 5 or `helpers/gates.py`'s `REASON_POLICY` changes. They cannot both stay as they are.

## Traceability

| Artifact | Reference |
|----------|-----------|
| Design | Not yet generated — to be built via `rfc-artifact-build` once D2 is settled |
| Tasks | Not yet generated — see above |
| Supersedes | N/A |
| Amends | `agents/contracts/lang-01.yaml` (LANG-01-C2), `agents/contracts/ocr-01.yaml` (OCR-01-C3), `agents/contracts/flat-01.yaml` (FLAT-01-C3), `agents/contracts/flat-03.yaml` (FLAT-03-C2), `agents/contracts/index-01.yaml` (INDEX-01-C2), `agents/contracts/conv-01.yaml` (CONV-01-C5) |
| Hard rule at issue | `CLAUDE.md` Hard Rule 5 (D2); Hard Rule 2 (D5) |
| Prior art | [[RFC-047]] (gate-layer correctness), [[RFC-044]] (routing authority), [[RFC-030]] (persist-with-FAIL position), [[RFC-043]] (erasure hardening), [[RFC-041]] (lifecycle lint) |
| Evidence | [[rfc047-d9-final-baseline]] (0 FAIL / 25 docs, 2026-09-22 — D2 blast radius); `scripts/gates/contracts.sh` output PASS=64 FAIL=2 |
| Memory | [[contracts-gate-greps-bytecode]] (D6), [[test-suite-reduction-2026-09]] (why label drift was the expected diagnosis) |
