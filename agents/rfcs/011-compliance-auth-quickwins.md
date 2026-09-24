<!-- Space: CITRA -->
<!-- Title: RFC-011: Compliance & Auth Quick-Win Batch -->
<!-- Folder: RFCs -->

---
id: RFC-011
title: Compliance & Auth Quick-Win Batch
status: proposed
date: 2026-07-16
plan-impact: yes
supersedes-decisions-in: []
---

## Traceability

| Artifact | Reference |
|---|---|
| Design Document | [design-rfc011-compliance-auth-quickwins.md](../designs/design-rfc011-compliance-auth-quickwins.md) |
| Implementation Plan | [tasks-rfc011-compliance-auth-quickwins.md](../tasks/tasks-rfc011-compliance-auth-quickwins.md) |
| Audit | [DOCSTORE_AUDIT_REPORT.md](../../audit/DOCSTORE_AUDIT_REPORT.md) |

## Context

`audit/DOCSTORE_AUDIT_REPORT.md` (2026-07-15) flagged 6 issues touching the erasure
cascade (HR2) and authentication/AGPL posture (HR3, HR4). All 6 were re-verified
2026-07-16 against live `HEAD` (`feat/scaling-pageindex`, post-RFC-010) by parallel
agents using codebase-memory-mcp + Serena in tandem, not from the audit's stale line
numbers. Two of the six are **already fixed** by RFC-010/RFC-007-D2 work and are
closed here, not re-implemented. The remaining four are small (4-15 line), standalone,
config-driven fixes — no shared surface with each other, batched together only
because they are all fail-closed/observability hardening around compliance-critical
code paths.

### What this RFC covers

| Issue | Status | File:Line | One-liner |
|---|---|---|---|
| ISS-02 | **Already fixed** (RFC-010/007-D2) | `storage.py:255-274` | Registry delete now `asyncio.wait_for`-bounded, not fire-and-forget |
| ISS-40 | Open | `registry.py:208-216` | `delete_doc` has no statement-level timeout; cascade-level `wait_for` doesn't guarantee server-side cancellation |
| ISS-41 | Open — HR2 violation | `storage.py:160-281` (cascade), `:613` (write site) | `preloaded/<filename>` raw object is written on ingest but never purged by the erasure cascade |
| ISS-32 | Open | `auth.py:39-47` | Unset bearer token fails **open** (all non-metrics/upload traffic unauthenticated) |
| ISS-35 | Open (metric-only scope) | `converters.py:1236`, `metrics.py` | No counter/alert when the AGPL (pymupdf4llm) fallback fires — three-puller confirmed via `uv.lock` |
| ISS-33 | Open | `config.py:71` | No startup assertion that a PII-flagged corpus is routed through a ZDR-compliant `openai_base_url` |

### What this RFC does NOT cover

- **ISS-35 hard gate** (`PDF_CONVERTER_STRICT` refusing to start / refusing to fall
  back to pymupdf4llm at all) — audit and verification agent both flag this as a
  legal-sign-off item under HR4 ("a legal decision to clear, not a settled safe-harbor"),
  not an engineering call. Tracked as a follow-up once legal confirms whether AGPL
  network-serving is acceptable for this deployment.
- ISS-41's already-erased backlog (documents erased *before* this fix ships still have
  orphaned `preloaded/<filename>` objects). A one-time orphan-sweep script (audit's
  "Approach B") is separate follow-up work, not part of this RFC's inline fix.
- Registry schema changes — none of these fixes touch `doc_registry` columns (that's
  RFC-014's territory).

## Hard Rule constraints (CLAUDE.md — binding)

- **HR2** — ISS-41 is a direct HR2 gap: "deleting the raw upload does NOT auto-remove
  derivatives... Purge MinIO `uploads/`, `processed/*.json`, `processed/*.meta.json`,
  Redis cache... explicitly, in that order." `preloaded/` is a raw store the rule's
  intent covers and the current cascade misses. D2 adds it as step 7, preserving the
  existing explicit per-store ordering.
- **HR3** — ISS-33 converts the existing HR3 *convention* (a code comment) into a
  *startup-time enforcement*. No routing logic changes; `OPENAI_BASE_URL` remains the
  lever.
- **HR4** — ISS-35 stays in metric-only scope this RFC; the hard-gate variant is
  explicitly deferred pending legal sign-off, per HR4's own text.

## Decision

### D1 — ISS-02: no code change, close as resolved

`storage.py:160-281`'s cascade step 6 already awaits `_registry_delete_doc` with
`asyncio.wait_for(..., timeout=settings.registry_delete_timeout_s)`, appending both
`TimeoutError` and generic `Exception` to the cascade's `errors` list. Regression
coverage exists: `tests/test_storage_contract.py:388` (`test_delete_doc_awaits_registry`),
`:404` (`test_delete_doc_registry_timeout`), `:480` (Postgres-failure scenario). No
task required beyond marking ISS-02 closed in the audit tracker.

### D2 — ISS-41: purge `preloaded/<filename>` in the erasure cascade

`sync_preloaded_to_minio()` (`storage.py:613`) writes `preloaded/{f.name}`, keyed by
filename, not doc_id — a raw object the cascade never visits. `doc_name` is already
resolved early in the cascade (`storage.py:177-181`, with a flat-doc basename fallback
at `:196-200`), so the key needed to build `preloaded/{doc_name}` is already in scope.

Add a step 7, after the registry delete, mirroring the existing per-step `S3Error`
handling pattern:

```python
# 7. preloaded/<filename> raw object (HR2: raw store must join the cascade)
if doc_name:
    try:
        mc.remove_object(settings.minio_bucket, f"preloaded/{doc_name}")
        logger.info("ERASE %s step7: removed preloaded/%s", doc_id, doc_name)
    except S3Error as e:
        if getattr(e, "code", "") != "NoSuchKey":
            errors.append(f"preloaded/: {e}")
else:
    logger.warning("ERASE %s step7: doc_name unknown; cannot clear preloaded object", doc_id)
```

Update the cascade docstring (`storage.py:161-166`) to enumerate step 7.

### D3 — ISS-40: statement-level timeout on registry delete

`registry.py:208-216`'s `delete_doc` issues a bare `await pool.execute(_DELETE_SQL, doc_id)`.
The cascade-level `asyncio.wait_for` from D1/ISS-02 bounds the *await*, but asyncpg
cancellation on timeout does not guarantee the server-side statement is actually
terminated. Add the same timeout at the statement level, reusing the existing
`registry_delete_timeout_s` config value (`config.py:34,87`) — no new config needed:

```python
async def delete_doc(doc_id: str) -> None:
    pool = get_pool()
    if pool is None:
        return
    await pool.execute(_DELETE_SQL, doc_id, timeout=settings.registry_delete_timeout_s)
    logger.info("registry: deleted doc_id=%s", doc_id)
```

### D4 — ISS-32: bearer auth fails closed by default

`auth.py:39-47` passes every non-`/metrics`,`/upload` request through when
`settings.mcp_bearer_token` is unset, after logging a once-per-process warning and
setting the `MCP_AUTH_DISABLED` gauge. Observability already exists; enforcement does
not. Add an explicit opt-in flag, defaulting to fail-closed to match `upload_app.py`'s
`require_api_key`:

```python
if not token:
    MCP_AUTH_DISABLED.set(1)
    if not settings.mcp_allow_unauthenticated:
        return JSONResponse({"error": "auth not configured"}, status_code=503)
    _warn_once_auth_disabled()
    return await call_next(request)
```

New config: `MCP_ALLOW_UNAUTHENTICATED` (bool, default `false`) in `config.py`,
alongside the existing `mcp_bearer_token`.

### D5 — ISS-35: AGPL-fallback observability (metric only)

`pdf_markdown_converters()` (`converters.py:1236`) unconditionally lists
`pymupdf4llm` as the chain base; Docling is prepended/appended only when importable.
`uv.lock` confirms pymupdf enters via three independent paths (docling-hierarchical-pdf,
the pageindex git fork, pymupdf4llm) — moving pymupdf4llm behind the `agpl-fallback`
extra does not detaint the venv. No counter exists today (`grep AGPL/pymupdf metrics.py`
is empty). Add one, following the file's `pageindex_<domain>_<noun>_total` naming
convention:

```python
AGPL_FALLBACK_TOTAL = Counter(
    "pageindex_agpl_fallback_total",
    "PDF conversions that used the AGPL pymupdf4llm path",
    ["reason"],
)
```

Increment inside the pymupdf4llm converter function with `reason="operator_configured"`
when `PDF_CONVERTER=pymupdf4llm` is explicit, else `reason="docling_missing"`. Alert on
`docling_missing > 0` — that's the unintentional-fallback signal. The hard gate
(`PDF_CONVERTER_STRICT`) is explicitly out of scope (see "What this RFC does NOT cover").

### D6 — ISS-33: startup ZDR-routing assertion for PII-flagged corpora

No enforcement exists today beyond a comment at `config.py:71`. Add a startup check in
`server.py`'s lifespan (`_lifespan_with_scrape`, lines 49-92): if a `PII_CORPUS` flag
is set, assert `settings.openai_base_url` matches a documented ZDR allow-list
(Azure modified-abuse-monitoring host / Bedrock / OpenAI EU-ZDR endpoints — the same
ladder documented in memory `rfc004-open-questions-research` Q5) or refuse to start:

```python
if settings.pii_corpus and not _is_zdr_allowlisted(settings.openai_base_url):
    raise RuntimeError(
        f"PII_CORPUS=true but openai_base_url={settings.openai_base_url!r} "
        "is not on the ZDR allow-list (HR3)"
    )
```

The allow-list constant lives in `config.py` next to the other routing config, documented
inline with the source ladder.

## Implementation Plan

All four fixes (D2-D6) are standalone and independently shippable — no ordering
dependency between them. Suggested sequence, smallest/highest-signal first:

1. D4 (ISS-32, ~12 lines) — fail-closed auth default
2. D2 (ISS-41, ~10 lines) — preloaded/ purge
3. D3 (ISS-40, ~4 lines) — registry statement timeout
4. D5 (ISS-35, ~10 lines) — AGPL metric
5. D6 (ISS-33, ~15 lines) — ZDR startup assertion

D1 (ISS-02) requires no implementation — mark resolved in the audit tracker only.

## Test Strategy

| Decision | Test |
|---|---|
| D2 | Extend `tests/test_storage_contract.py:87` (`test_erase_01_c1_cascade_order_across_stores`) to assert `remove_object` is called on `preloaded/<name>`; add a `doc_name is None` warning-path case |
| D3 | Extend `tests/test_registry_contract.py` to assert `pool.execute` receives the `timeout` kwarg |
| D4 | Two new auth-middleware tests: flag unset + token unset → 503; flag set + token unset → pass-through with warning |
| D5 | Assert counter increments with the correct `reason` label on both the explicit-config and docling-missing paths |
| D6 | Startup-assertion pass case (ZDR-allowlisted base_url) and fail case (arbitrary base_url) |

## Risks

- D4 (auth default flip) is the one behavior-changing fix in this batch — any
  deployment currently running with an unset bearer token in a trusted-network
  context will start rejecting requests until `MCP_ALLOW_UNAUTHENTICATED=true` is set
  or a token is configured. Flag in the deploy runbook before shipping.
- D6 depends on a documented ZDR allow-list existing as a reviewed artifact, not just
  inline code — get sign-off on the host list before merging, not after.
