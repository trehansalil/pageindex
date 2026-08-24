# Architectural Defect Zones Audit — 2026-08-11

**Question:** Which parts of the architecture are themselves generating the recurring fix→regress cycles — where one layer disables another, gates fight gates, and every RFC's fix seeds the next run's regressions?

**Method:** 13-agent workflow. Phase 1 mined 71 causal "fix X broke/disabled Y" chains from RFCs 019–036, corpus audit runs 6–19, the cross-cutting investigation reports, and cross-session memory (4 Sonnet miners). Phase 2 built three independent code-architecture maps — two via the codebase-memory graph, one via raw-source reading of the same files (Serena MCP was unreachable this session; raw-source served as the second parallel substrate). Phase 3 (Opus, high effort) correlated history against code, re-verifying every code citation directly against the working tree, and ranked 7 defect zones. Phase 4 ran the code-simplifier agent on the top 5 zones to produce restructuring proposals. **No code was modified.**

**Headline:** the recurring bugs are not independent defects. Seven structural zones account for ~68 attributed historical bugs, and three mechanisms dominate:

1. **Untyped string/ordinal contracts between layers** (verdict reason strings, `<!-- image -->` marker counts) — producers and consumers drift independently, and nothing detects a signal that is defined-but-never-consumed or consumed-but-never-produced.
2. **Order-dependent decision cascades** (`classify_verdict`, `pdf_to_markdown_docling`, `client.index()`) — the meaning of any branch/stage is everything above it, so every local fix silently re-decides unrelated documents.
3. **No single source of truth** — duplicated detectors with divergent thresholds, four whole-object writers to one meta sidecar, env flags read in two modules with different defaults, and a deploy/scoring loop that cannot distinguish "implemented" from "active."

---

## Zone 1 (critical, ~12 bugs): the `reason` string protocol — an untyped control channel spanning helpers.py → client.py

`validate_tree()` returns a bare magic string that simultaneously means (a) a diagnosis, (b) a routing instruction, and (c) a persistence verdict. Six hand-maintained membership tuples in `client.py` branch on it, and `client.index()` — a ~1,325-line god function (`client.py:808`–2135, `# noqa: C901, PLR0915`) — **forges the string seven times** purely to steer routing. There is no enum, no registry of valid reasons, no exhaustiveness check.

**Mechanism.** Producer and consumer live in different modules with no shared type, so three failure modes are structurally guaranteed and each has fired repeatedly:

- **Defined-but-never-consumed:** a new reason in helpers.py falls through every tuple to the persist-with-FAIL fallthrough at `client.py:2012` or a terminal raise, regardless of intent. RFC-029 added four new reasons with no client.py handling → 3 PASS→ERROR regressions in Run 13 (RFC-030 D2). RFC-018 D3b's `node_garbling` was never wired into any recovery trigger until RFC-025 D3.
- **Consumed-but-never-produced:** `_check_bidi_coherence` returns `"visual_order_garble"` (`helpers.py:1105`) but `validate_tree` discards it and returns `"bidi_degraded"` (`helpers.py:1395`). All four client.py branches keyed on `"visual_order_garble"` (1219 OCR escalation, 1493 VLM, 1532 Tesseract, 2002 terminal) are **unreachable dead code**, while the reason actually produced lands in the RFC-030 D2 fallthrough by accident.
- **Forged signals:** six sites overwrite `reason="node_count<3"` (`client.py:1480, 1544, 1570, 1678, 1706, 1793`) and one forges `reason="garbling"` (1764) to reuse a downstream branch — so the persisted `verdict_reason` is a routing artifact, not a measurement.

Compounding: only 1 of 5 `validate_tree` call sites (`client.py:1191`) passes `page_count`; the four post-recovery re-validations (1292, 1435, 1520, 1637) omit it, so the `suspect_density` gate (`helpers.py:1431`) is **silently off for exactly the scanned documents that needed a retry**.

**History:** RFC-018 D3b→RFC-025 D3; RFC-019 D2 `expected_script` never threaded (RFC-020 root cause); RFC-029→RFC-030 D2; RFC-027 D7 timeout function written-but-never-called (RFC-028 D0, 3 consecutive run failures); RFC-026 D5 garble check masked by structural early-exit; RFC-034 D18→RFC-036 D1 unhandled `PersistenceNotVisibleError` triggering full arq retry.

## Zone 2 (critical, ~11 bugs): `classify_verdict` — a 220-line first-match-wins cascade whose semantics are its branch order

`helpers.py:1614–1835`: ~15 sequential early-return branches, 8 env thresholds read inline, branch order re-shuffled by at least three separate RFCs. It re-derives what `validate_tree` already computed — **with different arguments**, so gate and verdict can disagree about the same tree.

**Verified generative couplings:**

1. `_tree_is_garbled(structure)` at `helpers.py:1701` is called **without** `expected_script`, while `validate_tree` calls it **with** it (`helpers.py:1343`) — the same tree can be garbled to the gate and clean to the verdict.
2. The RFC-022 B2 image-enrichment rescue is hoisted (1670–1697) **above** the `max_leaf_ratio > 0.75` hard FAIL (1699–1701): one enriched image with ≥500 clean chars can PASS a tree that is 100% a single leaf. This bypass reopened **four times** (Runs 9, 10, 16, 19).
3. RFC-036 D6's depth-adequacy check (1740–1745) sits *inside* the base PASS branch — it preempts every category promotion below it and is itself preempted by the hoisted enrichment branch.
4. `PASS_HYSTERESIS_BAND` (1726–1728) makes the verdict a function of stored history rather than the artifact.
5. **Feedback loop:** `PASS_MAX_LEAF_RATIO` is dual-purpose — `helpers.py:2319` reuses the same env var to decide whether to paragraph-split a leaf, so tuning the scoring threshold changes the tree *shape* that produces the metric being scored.
6. The flat branch calls `classify_verdict(..., None, ...)` at `client.py:1915` — `None` disables every reason-driven hard FAIL (`helpers.py:1631–1651`) **for all flat-routed documents**.

**History:** four consecutive RFCs widened `PASS_MAX_LEAF_RATIO` (0.17→0.20→0.30) for Docling jitter; RFC-024's widening let 81/132 garbled nodes PASS with an empty `verdict_reason`; RFC-022 B1/B2; RFC-036 D0→D4 within one RFC.

## Zone 3 (critical, ~10 bugs): the `<!-- image -->` ordinal contract and shared-mutable PictureResult dicts

The i-th marker ↔ `pics[i]` correspondence is an implicit invariant with **three producers able to violate it and three consumers responding to violation in three incompatible ways**:

- Consumer A, `splice_picture_text_for_tree` (`converters.py:2537`): all-or-nothing — on count mismatch it returns md unchanged, silently discarding **every** picture OCR text on the tree route.
- Consumer B, `splice_figure_markers` (`converters.py:2575`): degrades gracefully, splices by ordinal.
- Consumer C, `_enrich_image_blocks` (`client.py:742–745`): indexes positionally.

The violation is manufactured by the pipeline itself: `converters.py:3456–3462` appends one content-free landscape pseudo-`PictureResult` per landscape page *after* `_recover_picture_results` returned a dense list — the in-code comment claims these are inert, which is true of consumer B and **false of consumer A**. Any landscape-fallback document loses **all** tree-path picture OCR, including correctly-recovered unrelated pictures.

Both splices also `pop("ocr_text")` off the **shared** dicts; `_enrich_image_blocks` pops `png_bytes`. Since the tree splice (`client.py:1064`) runs before the flat splice (`client.py:1753`) on the *same* `pic_results`, a tree→flat reroute silently strips OCR from the flat artifact. Correctness depends on call order and on each consumer knowing what the previous one deleted.

**History:** RFC-017 refactor → tree-to-flat collapse of 5 Arabic scanned PDFs (RFC-020 F0); RFC-020 F2+D2 forced OCR → Docling reclassifies PictureItems → 0 PictureResults → F0 guard hard-fails; RFC-018 D0 + RFC-019 D1 filters → zero enrichment (RFC-020 F1); RFC-035 landscape → RFC-036 D0/D4; RFC-034 D19 displacement fix staged-but-uncommitted through Run 19; fabricated duplicate PictureResults to satisfy the count guard (CROSS_CUTTING Issue 1).

## Zone 4 (high, ~9 bugs): `pdf_to_markdown_docling` — a linear overwrite pipeline with duplicated candidates and no provenance

`converters.py:3182–3461`: a 280-line sequential mutation of one `md` string through ~14 stages, each wrapped in fail-open `except Exception → warn → continue` (**34 such blocks in converters.py, 19 in client.py, 1 in helpers.py**). No stage records what it changed.

- **Order is load-bearing but undocumented:** `_document_level_text_fallback` (3429) appends the whole pdfium text layer *before* `_recover_picture_results` (3442) measures containment — so every region's clip text is "already exported" and the RFC-024 D1 chart-recovery path is **suppressed for exactly the image-dominant documents it was written for** (a live bug). `_splice_landscape_fallback` (3436) injects markdown between them, feeding Zone 3.
- **Dual mirrored pipelines:** normalization applied to both `post_md` and `raw_md` at 3383–3391; any new step must be added at two mirrored sites or the candidates diverge.
- **Inject-then-discard:** heading injectors emit levels that `_recover_heading_depth` (857–877, a three-stage each-overwrites-the-last cascade) immediately re-derives.
- Two disagreeing landscape definitions: `_detect_page_rotation` (`converters.py:1937`) vs `_probe_landscape_pages` (`converters.py:2040`).

**History:** RFC-029 D3 fence toggle → 89–100% content loss (RFC-030 D0); RFC-034 D11 ToC filter collapsed the Penal Code 493/595 nodes flattened (fixed one revision later, D16); RFC-033 D2 detector had 0% TPR because upstream NFKC (`converters.py:2357`) decomposed exactly what it looked for (RFC-034 D6/D7); RFC-027 D4→D28 D1→D29 D1 heading-injection chain.

## Zone 5 (high, ~9 bugs): duplicated, divergent detectors — garble ×2, RTL ×6, Arabic ranges ×5, env constants ×3

- **Six RTL/reversal detectors** with different sampling rules (`converters.py:119, 1452, 1483, 1616`; `helpers.py:1054` — 5 samples, ≥0.4; `helpers.py:1257` — 8 samples, >0.3). The stage that *repairs* order (converters) does not share a criterion with the stage that *fails the document* for bad order (helpers).
- **Five Arabic codepoint-range definitions** (`converters.py:1013, 1603`; `helpers.py:908, 1075, 1142`) feeding ratios compared as if commensurable.
- **Three env vars read in two modules**, one with different defaults: `RFC029_MIN_CHARS_PER_NODE` = "500" at `client.py:391` (dead) vs "150" at `helpers.py:1124` (live); `RFC029_FLAT_PREFER_MULTIPLIER` defined-unused in helpers, live in client; `_OCR_ESCALATION` copy-pasted with a stale cross-reference comment.
- **Fan-in:** `_is_garbled_blob` (`helpers.py:875`) is the oracle for six semantically distinct decisions — tuning any of its nine ORed prongs moves all six.
- Root structural cause: a **circular import** (`helpers.py:16` imports converters at module level; converters imports helpers function-locally at 1503, 1808, 1906) that made copy-paste the path of least resistance.

**History:** ISS-36 duplicated thresholds; RFC-015 D8 `_MIXED_SCRIPT_RE` ASCII-space bug (would have flagged most clean Arabic as garbled; 489/489 tests green, caught only manually); RFC-033 D2 0% TPR instrument misread as a clean bill of health; German FAIL→PASS flip on byte-identical input because `_script_from_filename` returns None for German; the space-separated Latin-gibberish recall gap surviving targeted patching across 4+ RFCs.

## Zone 6 (high, ~9 bugs): multi-writer persistence — four whole-object writers to one sidecar, asymmetric write barrier

`processed/<doc_id>.meta.json` has **four independent writers, none of which merges**: `client.index` (`client.py:2102`), `save_flat_doc` (`storage.py:289`), `registry_backfill` (`registry_backfill.py:202, 339`), `promotion_sweep` (`promotion_sweep.py:90`). `save_doc_meta` (`storage.py:495`) always builds a fresh dict from three hand-maintained tuples and overwrites — no read-modify-write, no etag, no version.

- A `promotion_sweep` run **deletes** sha256, doc_description, node_count, extraction_route, inspector_class, and more (its payload is a 9–10 key subset), and recomputes the verdict with strictly less information (`classify_verdict(structure, content_class, None)` at `promotion_sweep.py:72`) — a FAIL stored for `low_content_density` can be promoted to PASS on a pipeline-version bump.
- **Barrier asymmetry:** `save_doc` and `save_doc_meta` call `_confirm_write_visible`; `save_flat_doc`'s *body* put (`storage.py:275–281`) does not, while its sidecar does — a listing can see the sidecar before the body exists, precisely the race RFC-034 D18 was added to close. Barrier exhaustion is swallowed as a warning (`storage.py:589–591`).
- Tree-doc verdicts reach Postgres only via the reconcile cron reading the sidecar — a second, unordered writer to the same row (`registry.py:184` COALESCE preserves NULL).

**History:** RFC-025 hysteresis structurally dead for three RFCs (snapshot never called before MinIO wipe, RFC-033 D0); persistence races recurring every run — RFC-033 D3 read-retry → RFC-034 D18 barrier → the barrier itself causing RFC-036 D1's ERROR-despite-PASS; the audit harness's own `includes('error')` substring bug defaulting all 24 docs to ERROR across runs 7–9.

## Zone 7 (high, ~8 bugs): state-vs-code skew — dark flags, stale deploys, stale docstrings, unscrapable metrics

What actually executes in a corpus run is not derivable from reading the working tree:

- **Flags that silently neuter other flags:** `REGION_AWARE_TEXT_CHECK_ENABLED` (default true, `converters.py:1691`) routes around `_TEXT_LAYER_GARBLE_CHECK_ENABLED` (also default true) — RFC-023 D0's whole contribution is dormant. `ALLOW_AGPL_FALLBACK=false` disables rotation normalization, the landscape probe, all picture recovery, the D3a probe, and `pdf_page_count` (hence `suspect_density`) at once, with no marker in the sidecar.
- **Stale invariants:** `probe_conversion_route`'s docstring (`converters.py:2842–2845`) claims shadow mode "NEVER influences routing" — yet the value forces full-page OCR (`client.py:885–899`), multiplies the child timeout ×16.5 (`worker.py:320–334`), and relaxes the cat_c threshold ×1.2 (`helpers.py:1795–1796`).
- **Computed-and-discarded:** `PRE_GARBLE_FORCE_OCR_ENABLED` defaults false (`client.py:941–943`) — the D3a probe runs, logs, and is thrown away.
- **Unobservable:** the worker's Prometheus registry is never scraped (`worker.py:644`); `WRITE_BARRIER_EXHAUSTED`, `LOW_QUALITY_TREES`, `OCR_ESCALATION_TOTAL` increment where nothing reads them.
- **Deploy skew:** the remote Docling service ran a 2026-07-30 build until 2026-08-07 — RFC-033's fixes were validated against a build that did not contain them; RFC-034 D19 sat staged-but-uncommitted through Run 19 and was scored as absent.
- Also: `ensure_tessdata` silently substitutes deu/eng for missing Arabic traineddata (ISS-34); `auth.py` fails open while `upload_app.require_api_key` on the same process fails closed (ISS-32).

---

## Simplification proposals (code-simplifier agent, top 5 zones — proposals only, nothing applied)

### Zone 1 — split measurement from routing; one policy table

Replace the overloaded reason string with a `TreeDefect` StrEnum + frozen `TreeGateResult(ok, defect, detail)` returned by `validate_tree`, and a separate `route` variable in `index()` that recovery steps set **instead of forging fake defects**. All six client.py membership tuples collapse into one `REASON_POLICY: dict[TreeDefect, Policy]` in helpers.py with an **import-time exhaustiveness assertion** — a new defect cannot exist without a declared disposition. Bind gate parameters once (`partial(validate_tree, expected_script=…, page_count=…)`) so the 4-of-5 `page_count` drift is unrepresentable. Keep `str(result)` byte-identical to legacy strings so persisted `verdict_reason` and tests don't move. Net ~LOC-neutral. Prevents outright: RFC-029→030 D2, RFC-018 D3b→025 D3, the `visual_order_garble` dead branches, RFC-019 D2 parameter drift. **Effort ~2.5–3.5 days + one corpus run.** Only corpus-visible step (enabling `suspect_density` on re-validations) ships behind a default-off flag.

### Zone 2 — signals object + grouped rule table

Compute every metric exactly once in a frozen `TreeSignals.from_tree(structure, expected_script, page_count)` shared by gate and verdict (deleting the `expected_script` disagreement by construction), read all 8 thresholds once via `VerdictThresholds.from_env()`, and rewrite `classify_verdict` as grouped rules — HARD_FAILs, then PROMOTIONs, then CAPs — where within-group order is semantically irrelevant, so hoisting can no longer silently re-verdict other documents. The enrichment rescue becomes a named `exempt_when` field on the one FAIL rule it exempts; depth-adequacy becomes a CAP applied to *all* promotions; hysteresis becomes an explicit post-hoc `pass_hysteresis` promotion visible in `verdict_reason`; the metric↔shape feedback loop is severed by giving `_blank_line_fallback_enabled` its own env var defaulting to the old one. Flat path stops passing `validate_reason=None` (re-arming hard FAILs for flat docs — expected corrections, not regressions). Golden-verdict harness first; zero-diff for mechanical steps, per-doc-justified diffs for the three behavior flags. **Effort ~3.5–4.5 days across two corpus cycles.**

### Zone 3 — one alignment function, one writer, routing signal out of the content channel

`_align_pics(md, pics) -> list[PictureResult | None]` (pad with None, never bail) shared by all three consumers; both splices become pure and return the set of consumed ordinals, which is threaded to `_enrich_image_blocks` — replacing the destructive `pop("ocr_text")` protocol. Landscape routing leaves `pic_results` entirely and travels as its own return value (deleting the manufactured mismatch and RFC-036 D4's downstream filtering). Deletes the duplicate-fabrication hack for standalone images. Net ~−70/+65 lines. Kills the whole "count guard fails → all tree OCR silently dropped" family (RFC-020 F0/F1/F2, the live landscape defect) and the tree→flat OCR-stripping latency bug. **Effort ~1.5–2 days.** One trap: the consumed-set must land in the same commit as the pop removal or reroutes duplicate chart text — regression test specified.

### Zone 4 — stage table + provenance instead of a linear overwrite

A 12-line `_run_stages` runner owns the fail-open contract (one except for all stages) and records per-stage char/heading deltas into `extraction_stages` in meta.json — so a stage that deletes 90% of a document is self-evident in the artifact (would have caught RFC-029 D3 and RFC-034 D11 at the stage that caused them). `_build_candidate()` collapses the mirrored post/raw pipelines to one call site. `_recover_picture_results` takes an explicit `body_for_containment` snapshot captured *before* the pdfium fallback appends — fixing (not documenting) the live RFC-024 D1 suppression. Rename the two landscape predicates to the different questions they actually answer. Net converters.py ~−110 lines. Sequence: byte-identical refactor (golden-hash proven) → observability → the one behavior fix behind a flag → never combine refactor and behavior change in one revision. **Effort ~2.5 days + one corpus run.**

### Zone 5 — one leaf module for script primitives; break the import cycle

New dependency-free `script.py` owning one Arabic range set, one readability score, one `order_verdict()` core that all six reversal detectors become thin parameterized wrappers over (sampling differences kept as explicit named args, not silently unified); the three shared env constants move to `config.py`, deleting the helpers↔converters circular import that caused the copy-paste in the first place. `_is_garbled_blob` becomes `garble_prongs() -> frozenset[str]` with six named one-line policies, so the six decisions stop sharing one calibration. A grep-based drift test fails CI if a second copy of any range/constant/`get_display` comparison reappears. Wave 0 is a characterization harness recording all detector outputs for every corpus fixture — waves gate on zero diffs. Net ~−250 lines from the two big files, +200 in leaf files. **Effort ~3.5–4.5 days; stopping after the module extraction + drift test alone is worth landing.**

### Zones 6–7 (no simplifier pass this round — recommended directions)

- **Zone 6:** single-writer-per-field discipline for meta.json — `save_doc_meta` becomes read-merge-write with a field-ownership map; `promotion_sweep` must never write a subset payload or recompute verdicts with fewer inputs than ingestion had; extend `_confirm_write_visible` to `save_flat_doc`'s body put; make barrier exhaustion a job failure, not a warning.
- **Zone 7:** one flag inventory read at job start and **persisted into the sidecar** (effective config + build sha already partially there), so every verdict records the pipeline that produced it; scrape or drop the dead worker metrics; fix the shadow-mode docstring or the behavior; make deploys carry the build sha end-to-end so an audit run can refuse to score against a stale build.

---

## Cross-cutting themes (distilled from 71 mined chains)

1. **Signal wiring gaps are the #1 recurring bug class:** new reasons/prongs/flags added in one module without wiring the consumer — RFC-020, 025, 027/028, 029/030, 032 are all instances of the same defect.
2. **Threshold widening is the single most recurring regression vector** (PASS_MAX_LEAF_RATIO 0.17→0.20→0.30 across RFCs 021–024), reopening Hard-Rule-5 violations earlier RFCs closed.
3. **Improvements and regressions consistently arrive paired in the same run** — because fixes share mutable state with adjacent safety nets (better OCR diluting the garble ratio that gated escalation; forced OCR destroying the PictureItem path the splice depends on).
4. **The audit/scoring cycle cannot distinguish "implemented" from "deployed/active"** — stale Docling images, staged-uncommitted fixes, dark flags, dead counters. Several "regressions" were accounting or timing artifacts, and the harness itself had an integrity bug.
5. **Detectors get validated by absence of firing** — a 0% TPR instrument read as a low false-positive rate (RFC-033 D2); 489/489 green tests while a regex flagged all clean Arabic as garbled (RFC-015 D8).
6. **`expected_script` is load-bearing but leaky** — every place it's hardcoded to None or defaults wrong silently disables an otherwise-correct heuristic for an entire language class.

## Recommended sequencing (sustainable path)

The zones are coupled: Zone 1 and Zone 2 share the reason vocabulary; Zone 3 and Zone 4 share the marker contract. Recommended order, each step independently landable:

1. **Zone 7 observability floor first (cheap, de-risks everything else):** persist effective flags + build sha into the sidecar; per-stage provenance from Zone 4's commit-2. Until the scoring loop can tell "implemented" from "active," every other fix's validation is suspect — this is what let RFC-033 be validated against a build that didn't contain it.
2. **Zone 1 (reason enum + policy table)** — highest bug-count, mostly mechanical, byte-identical persisted strings.
3. **Zone 3 (picture alignment)** — small (~2 days), kills a critical live defect (landscape fallback zeroing all tree OCR).
4. **Zone 2 (verdict signals + rule table)** — builds on Zone 1's enum; golden-verdict harness doubles as the permanent regression net for future threshold changes.
5. **Zone 5 (script.py leaf module + drift CI test)** — ends the copy-paste economy; the characterization harness doubles as the missing detector-quality test bed.
6. **Zone 4 remainder + Zone 6 (single-writer meta, symmetric barrier)** — last, as they benefit from the provenance and golden harnesses above.

Total estimated effort for zones 1–5: **~14–17 engineer-days plus ~4 corpus validation cycles**, roughly LOC-neutral overall, with the recurring-regression machinery (exhaustiveness assertions, golden harnesses, drift tests, provenance) left behind as permanent guardrails.

---

*Generated by a 13-agent workflow (4 Sonnet history miners, 3 Opus code mappers, 1 Opus synthesizer, 5 Opus code-simplifier agents); 71 causal chains mined; all code citations re-verified against the working tree by the synthesis agent. Full raw output: workflow run `wf_b36f633b-730`.*
