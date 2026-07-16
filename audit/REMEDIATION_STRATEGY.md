
# Docstore Audit Remediation Strategy

Status: proposed · Date: 2026-07-16 · Source: `audit/DOCSTORE_AUDIT_REPORT.md` (2026-07-15)

## Summary

The audit's 28 issues were re-verified against live `HEAD` on `feat/scaling-pageindex`
(post-RFC-010) by six parallel agents, each firing codebase-memory-mcp and Serena LSP
tools in tandem rather than serially. **7 of 28 issues are already resolved** by
RFC-010/RFC-007-D2 work that landed the same day the audit was written (ISS-02, ISS-03,
ISS-08, ISS-18, ISS-19) or were mischaracterized in the audit itself (the "no dependency
scanning" claim — see Gaps, below). The remaining 21 are batched into three RFCs by
theme and risk profile, plus a fourth RFC that builds the automated promotion mechanism
the audit's MARGINAL taxonomy never had.

| RFC                                                             | Theme                              | Issues                                                                                    | Est. size                        |
| --------------------------------------------------------------- | ---------------------------------- | ----------------------------------------------------------------------------------------- | -------------------------------- |
| [RFC-011](../.agents/rfcs/011-compliance-auth-quickwins.md)      | Compliance & auth quick-wins       | ISS-02(closed), 40, 41, 32, 35, 33                                                        | ~50 lines, 5 standalone fixes    |
| [RFC-012](../.agents/rfcs/012-reliability-deadcode-quickwins.md) | Reliability & dead-code quick-wins | ISS-03(closed), 07, 37, 39, 42, 45, 43, 46                                                | ~40 lines + 2 deletions          |
| [RFC-013](../.agents/rfcs/013-structural-hardening.md)           | Structural hardening               | ISS-08/18/19(closed), 05, 44, 34, 36                                                      | ~60 lines, 1 corpus revalidation |
| [RFC-014](../.agents/rfcs/014-corpus-promotion-pipeline.md)      | Automated corpus promotion         | new mechanism; unblocks سياسة حوكمة, Haftpflicht-Besondere; gates مرسوم 33 | ~150 lines + schema migration    |

Every fix in RFC-011/012/013 is independently shippable — there is no cross-RFC
sequencing dependency except RFC-013 D6 (ISS-34, tessdata raise) pairing with a
non-code `ara.traineddata` pre-bake for full effect, and RFC-014 depending on nothing
in the other three RFCs (it only needs `helpers.py`'s existing tree-walk helpers).

## Recommended sequencing

An L7 read of blast radius and payoff, not the audit's original batch numbering:

1. **RFC-011 D4 (ISS-32, auth fail-closed) first, alone.** It's the one
   behavior-changing fix in the whole set — any deployment running with an unset
   bearer token today will start rejecting traffic. Ship it with a deploy-runbook note,
   watch it in isolation before landing anything else.
2. **RFC-011 remainder + RFC-012 entire batch**, together — both are collections of
   small, config-driven, non-interacting fixes with no shared blast radius. Natural
   half-day batch, matching the audit's own Batch-1 sizing.
3. **RFC-013 D4/D5 (ISS-05, ISS-44)** — pure performance/dedup, ship anytime, no
   corpus risk.
4. **RFC-013 D6 (ISS-34) + tessdata pre-bake**, sequenced together — the raise
   shouldn't ship ahead of the pre-bake or Arabic ingestion starts failing loud instead
   of silently mojibake-ing.
5. **RFC-013 D7 (ISS-36, garble dedup) + mandatory corpus revalidation** — the one item
   in this whole plan with real regression risk given the GHV-TKV-Tarif false-positive
   history. Don't close it out on green tests alone.
6. **RFC-014**, once RFC-013 D7's revalidation is stable — the promotion mechanism's
   first sweep is more informative once the garble-gate dedup has already landed,
   since D7 touches the exact signal RFC-014's `classify_verdict` depends on.

## Promotion criteria for MARGINAL documents (RFC-014 summary)

The audit's PASS/MARGINAL/FAIL taxonomy (`audit/SCOPE.md` §5) existed only as a
manual, hand-computed judgment applied fresh in every audit pass — no code computed
leaf concentration, no stored verdict persisted anywhere, no trigger re-checked a
MARGINAL document once a fix shipped. RFC-014 closes that gap:

- **What "promoted" means, mechanically:** a document's verdict flips from MARGINAL to
  PASS when a newly-added `classify_verdict()` helper (built on a new
  `_tree_max_leaf_ratio` metric that doesn't exist in code today) evaluates it against
  category-specific numeric gates — `max_leaf_ratio < 0.15`, `node_count >= 3`, no
  garbling, plus category-specific noise-ratio checks for OCR-rescued and
  text-quality-fixed documents.
- **What triggers a re-check:** every ingest/reprocess runs the classifier inline
  (idempotent, deterministic). For documents already in the corpus, a
  `pipeline_version`-gated backfill sweep re-classifies any stored document whose
  recorded version predates the current pipeline version — triggered automatically
  whenever a corpus-affecting fix bumps `CURRENT_PIPELINE_VERSION`, not on a schedule
  and not requiring a fresh manual audit.
- **What never auto-promotes:** Category D (Docling/source-limited) documents are
  locked `permanent_marginal` and require a human to explicitly clear the flag — the
  mechanism doesn't keep re-trying a fix that can't land. A document showing the
  audit's Category-E regression signature (node count drop >30% with leaf-concentration
  growth >2x) is blocked from promotion and raises a `verdict_regression` alert instead
  — this is the مرسوم 33 case, gated behind a required node-title diff before any
  verdict decision is made.
- **First concrete outcome:** سياسة حوكمة and Haftpflicht-Besondere both sit just above
  the literal 0.15 threshold (0.165 and 0.16 respectively) — RFC-014 recommends a
  documented 0.17 threshold for their category rather than a one-off manual override,
  so the mechanism promotes them on its own and would do the same for the next
  borderline document without a human re-running the audit.

## Gaps the original audit didn't cover

Verified via a dedicated gap-analysis pass against `EXPLORATION.md`, `ISSUES.md`, CI
config, and `uv.lock` — six findings, ranked by whether they're cheap to close now:

1. **The audit's own "no dependency/CVE scanning" disclaimer is inaccurate — fix the
   audit, not the codebase.** `scripts/gates/supply-chain.sh` (Gate 6) already runs
   `pip-audit` over `uv.lock` with a whitelist mechanism
   (`.agents/governance/known-advisories.yaml`), wired into
   `.github/workflows/build-push.yml`. This is a real, running control; the audit
   report's text should be corrected, not treated as an open item.
2. **`starlette==1.0.0` in `uv.lock` warrants a two-minute manual check.** Sitting next
   to `fastapi==0.135.3` (which normally vendors ~0.4x Starlette), this version number
   is suspicious — likely an unrelated PyPI namesquat/placeholder pinned incidentally
   rather than the real Starlette. Cheap to verify (check the lockfile's source/hash
   for that entry) before it's forgotten.
3. **CI only triggers on push to `master`, not on PRs** — not in the audit's
   disclaimed-gaps list but should be. Gates 7-8 (integration/e2e, need
   MinIO+Redis) don't run in CI at all today, only locally. Adding a `pull_request`
   trigger is cheap; getting e2e running in CI needs service containers and is
   Batch-2-sized follow-up work, not covered by RFC-011/012/013/014.
4. **`tracing.py` has thin audit coverage relative to its compliance role.** It
   appears only in ISS-08's file reference and one `EXPLORATION.md` mention, not in the
   core-pipeline file-by-file walk — despite carrying the Langfuse
   flush-failure/private-OTel-provider edge cases documented in prior-session memory
   (`langfuse-tracing-llm-02`). Flag for the next audit pass, not urgent enough to fold
   into this remediation cycle.
5. **`hash_cache_migrate.py` has zero audit coverage across all three audit passes.**
   It touches the cache/hash layer that HR2's erasure cascade depends on (Redis cache
   purge, step 4 of the cascade) and was never mentioned in `EXPLORATION.md`,
   `ISSUES.md`, or the report. This is the one genuine, unambiguous blind spot from
   this audit cycle — worth a standalone read before RFC-011's cascade fix (ISS-41)
   or any future cache-layer change lands.
6. **Everything else in the audit's own §7 disclaimer** (mutation testing, load
   testing, LLM prompt-quality evaluation, Docling/pymupdf4llm internals) is correctly
   and honestly out of scope for a code-inspection audit — no action recommended
   beyond what's already flagged there.

## What this strategy deliberately does not attempt

- No RFC in this batch touches the AGPL hard-gate decision (`PDF_CONVERTER_STRICT`) —
  that's explicitly a legal sign-off per HR4, not an engineering call this remediation
  cycle can close.
- No RFC re-litigates RFC-009's tree-walk performance work or RFC-007's registry
  integrity work — both are cited as already covering adjacent ground in RFC-010's own
  "does not cover" section and remain out of scope here.
- ISS-05's long-term registry-only listing fix (audit's Approach B, removing the MinIO
  fallback entirely) is intentionally deferred — RFC-013 ships only the
  bounded-concurrency interim, per the audit's own Batch-3 sizing.
