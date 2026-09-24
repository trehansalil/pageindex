---
id: "PLAN-TEST-BUDGET"
title: "Dead-Code Removal + Test Suite Under 1000"
type: plan
status: draft
date: "2026-09-22"
tags: [plan, dead-code, tests, governance]
aliases: ["Test Budget Plan", "Dead Code + <1000 Tests"]
---

## Objective

Two hard, measurable targets, in this order:

1. **Remove dead code** — production first, then test-local residue.
2. **Collected test count strictly below 1000.**

Everything else (file consolidation, gate repair) is a by-product, not a goal.

## Measured Baseline (2026-09-22, `ICR-97-rfc48-surya-image-fallback`)

| Metric | Value | Source |
|---|---|---|
| Collected tests | **2510** | `make test PYTEST_ARGS="--collect-only -q"` |
| — plain | 1997 | id has no `[…]` |
| — parametrized cases | 513 | id has `[…]` |
| Test files (`tests/test_*.py`) | 60 | |
| Test LOC | 44 449 | vs 31 371 src LOC |
| `__all__` facade entries | 409 across 10 packages | AST count |
| ruff dead-code signals, `src/` | 53 (42 ARG001, 8 ARG002, 3 ERA001); **0 F401** | `ruff check src --select F401,F811,F841,ARG,ERA` |
| ruff dead-code signals, `tests/` | 584 (269 ARG005, 196 ARG001, 75 ARG002, 66 F401, 16 ERA001, 9 F841, 3 F811) | same, `tests/` |

**Gates already red before we touch anything** — repairing them is inside this
plan's scope, not extra work:

- `test-ratio-guard` Check 1: ratio **0.83 (60/72)** vs ceiling `0.65` → need **≤ 46 test files**.
- `test-ratio-guard` Check 2: 11 test files added without `# ALLOW-NEW-TEST-FILE`.
- `unit.assertion_density: 2.0`: repo mean is ≈ **1.6 asserts/test**. Deleting
  shallow tests *raises* this — the reduction and the gate pull the same way.

**Gates that constrain the deletion** — every one must still pass at the end:

- `unit.coverage_default: 70`, `coverage_modules: client 90 / storage 90 / worker 85`.
- `contracts.all_contracts_in_tests`: 61 contract IDs across 19
  `agents/contracts/*.yaml` must remain grep-findable in `tests/`.
- `test-index-guard`: every `src/**` file mapped in `tests/TEST_INDEX.yaml`;
  every file listed there must exist. **Each merge edits this file in the same commit.**

## Prior Art That Must Not Be Re-Derived

- Dead-code audit **run 6** (2026-09-07, `audit/DEAD_CODE_AUDIT_2026-09-07.md`)
  found **zero dead production symbols**; the 399 non-entry-point `tests/`
  constants bucket is **closed**. Do not reopen either.
- The two measurement traps that invalidate naive verdicts: (a) facade import
  vs submodule import are different questions; (b) zero textual references is
  the *designed* state for `@pytest.fixture(autouse=True)`.
- **RFC-045** already adjudicated the facade shrink: **59 of 59 ruled REMOVE**,
  pending approval. `scripts/facade_surface_measure.py` re-measures on demand
  (today: 45 REMOVE under the narrow rule, 11 flips). Removal scope = the
  `__all__` entry **and** the import binding.
- Hard keep-list (deleting any of these is a *startup* failure): the three
  `services/docling-service/app.py` imports, the five `FEATURE_WIRINGS`
  producers (`converters.chunked_docling_timeout_s`,
  `converters.probe_conversion_route`, `converters.zdr_egress_gate`,
  `helpers.GATES`, `helpers.compute_image_enrichment_ratio`), and
  `worker.WorkerSettings`.

---

## Part A — Dead Code

### A0. Baseline capture (blocking, ~20 min)

`make test PYTEST_ARGS="--cov=pageindex_mcp --cov-report=json -q"` → commit the
JSON to `audit/baselines/`. Without a per-module coverage baseline there is no
way to prove Part B did not breach the 90/90/85 thresholds. Also record
`bash scripts/eval.sh` gate output as the "before" picture.

### A1. Mechanical residue (low risk)

- `ruff check --fix --select F401,F811,F841 tests/` — 78 findings, all
  auto-fixable or near.
- `src/`: 3 × ERA001 commented-out code — delete.
- ARG00x (537 total) is **not** dead code: unused args in mock/stub signatures
  are load-bearing. Do not mass-fix; leave as-is and note the exclusion.

### A2. Execute the RFC-045 facade shrink (the real dead code)

The only adjudicated dead production surface. 59 `__all__` entries + their
import bindings across `client`, `converters`, `helpers`, `registry_backfill`,
`storage`, `worker`.

Order per package: re-run `scripts/facade_surface_measure.py` → remove entry +
binding → update `FROZEN_SURFACE` in `tests/test_facade_surface_guard.py` in
the **same commit** → `make test PYTEST_ARGS="tests/test_facade_surface_guard.py -q"`
→ `make preflight` once at the end (the FEATURE_WIRINGS producers fail at
startup, not at import).

Blocker: RFC-045 is still awaiting approval. Confirm before executing.

### A3. Run 7 of `dead-code-discover-verify` (new surface only)

Run 6 predates RFC-046, RFC-047 and RFC-048, which shipped the Surya fallback,
the arbitration layer and the image OCR path. Scope run 7 to files touched
since `2026-09-07` — that is where new dead code can exist. Everything else is
already adjudicated. Apply both measurement traps before believing any verdict.

### A4. Non-`src/` artifacts

`stress_test.py`, `ingest_via_server.py`, `promotion_sweep.py`,
`scripts/table_separator_baseline.py`, and the ~40 superseded `audit/ZONE_DELTA_*`
reports. Verify each against `Makefile`, `.github/workflows/`, `docs/` and the
skills under `.claude/` before deleting — several are skill entry points.

---

## Part B — Test Count: 2510 → < 1000

Four mechanisms, in dependency order. Nothing here deletes a behavioral
assertion that is the *only* cover for a production branch; the coverage
baseline from A0 is the referee.

### B1. Source-scanning guards leave pytest → the static gate (~265 tests)

`test_architecture_guards.py` (79), `test_facade_surface_guard.py` (98),
`test_rfc042_measurement_guard.py` (8), `test_rfc045_facade_measurement.py` (5),
`test_no_naive_block_text.py` (1), `test_config.py::test_bool_fields_share_one_parse_predicate`
(28 cases), `test_verdict.py::TestPromotionLiteralsAreThresholdSourced` (21) and
`::TestVerdictGateThresholdConfigContract` (24).

These read source text with `ast`/`inspect`/filesystem scans. They are **lint
rules wearing a pytest costume** — they exercise no runtime behavior, and each
parametrized name costs a collected test. Move the logic into
`scripts/gates/source-invariants.py`, wired into `scripts/gates/static.sh`;
keep ~12 pytest tests covering the *guard script itself*.

**265 → ~12. Saves ~253.** Enforcement strength is unchanged — arguably stronger,
since the static gate runs before the unit gate.

### B2. Collapse golden-table parametrization (~130 tests)

A golden table asserted row-by-row buys one bit of extra locality per row and
costs N collected tests.

- `test_zone1_verdict_partition::TestGoldenPartitionTable` — 71 cases (34 + 34 + 3)
  → 3 table-driven tests that assert the whole table and report every mismatch.
- `test_triad_golden::TestTriadGoldenFiles` — 54 cases (5 aspects × ~10 files)
  → 10 (one full-snapshot test per golden file; the four per-aspect tests are
  strict subsets of `test_full_snapshot`).
- `test_gates::TestDefectFromReasonStr` round-trips (22) → 2.
- `test_verdict::TestMaxPriorityWinsSQL` (18) → 4.

**~165 → ~19. Saves ~146.**

### B3. Merge RFC-wave files into their module homes (~14 files, ~300 tests)

RFC-039 did one consolidation round; RFC-042→048 re-fragmented the suite. Each
wave file duplicates setup and re-asserts invariants its topical home already
covers. Merge, then dedupe on assertion identity:

| Wave file | now | merges into |
|---|---|---|
| `test_zone1_verdict_partition.py` | 92 | `test_verdict.py` |
| `test_observability_combined.py` | 58 | `test_obs_logging.py` |
| `test_rfc048_surya_image.py` + `test_d8_surya_fallback.py` | 37 + 17 | `test_ocr_fallback.py` (new home) |
| `test_converters_pipeline.py` + `test_converters_cli.py` | 32 + 10 | `test_converters.py` |
| `test_zone4_measurement.py` | 29 | `test_flat.py` |
| `test_rfc_tables.py` | 29 | `test_density_gate.py` |
| `test_rfc046_attribution.py` | 25 | `test_recovery.py` |
| `test_registry_backfill.py` | 25 | `test_registry.py` |
| `test_d7_arbitration.py` + `test_d7_arabic_density_floor.py` | 24 + 8 | `test_garble.py` |
| `test_rfc_registry.py` | 18 | `test_registry.py` |
| `test_rfc_reorder.py` | 18 | `test_helpers_combined.py` |
| `test_rfc_quality.py` | 18 | `test_gates.py` |
| `test_d4_corrective_retry.py` | 15 | `test_recovery.py` |
| `test_d6_flat_verdicts.py` | 12 | `test_flat.py` |
| `test_zone4_bidi_rtl.py` | 11 | `test_bidi.py` |
| `test_d10c_pre_nfkc_threading.py` | 11 | `test_converters.py` |
| `test_zone3_ocr_recovery.py` | 10 | `test_recovery.py` |

**~499 → ~200 after dedupe. Saves ~300.** Also takes the file count 60 → 43
(ratio 0.60 ≤ 0.65), which repairs `test-ratio-guard` Checks 1 and 3 and makes
Check 2 moot for 9 of the 11 unmarked files.

### B4. Depth trim in the ten heaviest survivors (~750 tests)

Only after B1–B3, and only with the A0 coverage baseline open. Per file, delete
in this priority order — stop the moment per-module coverage would move:

1. Tests whose assertion is implied by a sibling in the same class.
2. Single-assert tests re-checking a constant or a dataclass field (the 1.0–1.2
   density files: `test_hr3_zdr_egress` 1.2, `test_registry_backfill` 1.0).
3. Parametrized cases differing only in an irrelevant input dimension.
4. The 20 `skip`/`xfail` markers — a permanently-skipped test is dead code with
   a collection cost.

| File | now | target |
|---|---:|---:|
| `test_verdict.py` (incl. zone1) | 238 + 92 | 96 |
| `test_gates.py` (incl. rfc_quality) | 159 + 18 | 78 |
| `test_converters.py` (incl. pipeline/cli/d10c) | 144 + 53 | 75 |
| `test_garble.py` (incl. d7 ×2) | 120 + 32 | 55 |
| `test_bidi.py` (incl. zone4_bidi) | 105 + 11 | 45 |
| `test_registry.py` (incl. backfill, rfc_registry) | 92 + 43 | 55 |
| `test_obs_logging.py` (incl. observability_combined) | 90 + 58 | 60 |
| `test_hr3_zdr_egress.py` | 88 | 35 |
| `test_flat.py` (incl. zone4_measurement, d6) | 85 + 41 | 45 |
| `test_recovery.py` (incl. rfc046, d4, zone3) | 75 + 50 | 47 |
| all remaining files | 826 | ~400 |

### Arithmetic

| Step | Δ | Running total |
|---|---:|---:|
| Baseline | | **2510** |
| B1 guards → static gate | −253 | 2257 |
| B2 golden-table collapse | −146 | 2111 |
| B3 wave-file merge + dedupe | −300 | 1811 |
| B4 depth trim | −880 | **≈ 930** |

Target is **≤ 930 collected**, giving ~70 tests of headroom under the hard
limit of 1000 for tests added while this lands.

---

## Sequencing

| Wave | Content | Verification |
|---|---|---|
| 0 | A0 baseline + `--collect-only` snapshot | artifacts committed under `audit/baselines/` |
| 1 | A1 mechanical residue | `ruff check`, `make test` |
| 2 | A2 facade shrink (needs RFC-045 approval) | facade guard + `make preflight` |
| 3 | B1 guards → static gate | `scripts/gates/static.sh`, collect count |
| 4 | B2 golden-table collapse | `make test`, golden diff empty |
| 5 | B3 merges, one commit per merge, `TEST_INDEX.yaml` edited in the same commit | `test-index-guard`, `test-ratio-guard` |
| 6 | B4 depth trim, one commit per file | coverage diff vs A0 after each |
| 7 | A3 run 7 + A4 artifacts | dead-code audit report |
| 8 | Final: `scripts/eval.sh` all gates green | collect count < 1000 |

Waves 3–6 are independent of wave 2; if RFC-045 approval stalls, Part B
proceeds without it.

## Stop Conditions

Stop and report rather than pushing through, if any of these occur:

- Per-module coverage drops below 90 (client/storage), 85 (worker), 70 (default).
- A contract ID from `agents/contracts/*.yaml` loses its last grep hit in `tests/`.
- The assertion-density mean falls (it should only rise).
- A deletion needs a threshold in `verify-gates.yaml` relaxed to pass — that is
  an RFC amendment, never an edit.

## Risks

- **Deleting the only cover for a branch.** Mitigation: the A0 coverage
  baseline is checked after every wave-6 commit, not once at the end.
- **B4 is judgement-heavy** — "implied by a sibling" is the call most likely to
  be wrong. Mitigation: one commit per file, so a bad trim reverts in isolation.
- **Merge conflicts against active RFC-048 work** on this branch. Mitigation:
  land waves 3–6 after RFC-048 merges, or rebase per wave.
- **Test count is not test value.** A suite at 930 that misses a regression is
  worse than 2510. The coverage and contract gates are what keep this honest;
  the number alone is not the success criterion.
