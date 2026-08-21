"""Postgres document registry — query functions (RFC-006).

Write path, delete path, read path, Stage A/B filters, and backfill helpers.
"""

from __future__ import annotations

import logging
from typing import Any

from . import schema as _schema

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Write path
# ---------------------------------------------------------------------------

_UPSERT_SQL = """
INSERT INTO doc_registry (
    doc_id, doc_name, source_url, processed_at,
    content_class, sha256, product, tier, doc_family,
    effective_date, doc_description, node_count,
    verdict, pipeline_version, permanent_marginal,
    verdict_computed_at
) VALUES ($1, $2, $3, $4, $5, $6, $7, $8, $9, $10, $11, $12, $13, $14, $15, $16)
ON CONFLICT (doc_id) DO UPDATE SET
    -- Facet / descriptor columns: unconditional last-writer-wins.
    doc_name        = EXCLUDED.doc_name,
    source_url      = EXCLUDED.source_url,
    content_class   = EXCLUDED.content_class,
    product         = EXCLUDED.product,
    tier            = EXCLUDED.tier,
    doc_family      = EXCLUDED.doc_family,
    effective_date  = EXCLUDED.effective_date,
    doc_description = EXCLUDED.doc_description,
    -- Zone-4: processed_at CAS guard — prevent stale reconcile data from
    -- regressing sha256, node_count, processed_at.  COALESCE-to-empty-string
    -- mirrors the verdict CAS pattern so a row with NULL/empty processed_at
    -- always accepts an incoming value.
    processed_at = CASE
        WHEN EXCLUDED.processed_at >= COALESCE(doc_registry.processed_at, '')
        THEN EXCLUDED.processed_at
        ELSE doc_registry.processed_at
    END,
    sha256 = CASE
        WHEN EXCLUDED.processed_at >= COALESCE(doc_registry.processed_at, '')
        THEN EXCLUDED.sha256
        ELSE doc_registry.sha256
    END,
    node_count = CASE
        WHEN EXCLUDED.processed_at >= COALESCE(doc_registry.processed_at, '')
        THEN EXCLUDED.node_count
        ELSE doc_registry.node_count
    END,
    -- Zone-8: temporal CAS guard — prefer newer verdict_computed_at.
    -- Existing rows with NULL/empty verdict_computed_at always accept any
    -- incoming verdict (COALESCE to '' ensures '' < any ISO timestamp).
    -- When the incoming payload carries no verdict (empty string), preserve
    -- the existing verdict regardless of timestamps.
    verdict         = CASE
        WHEN EXCLUDED.verdict_computed_at >= COALESCE(doc_registry.verdict_computed_at, '')
        THEN COALESCE(NULLIF(EXCLUDED.verdict, ''), doc_registry.verdict)
        ELSE doc_registry.verdict
    END,
    pipeline_version = CASE
        WHEN EXCLUDED.verdict_computed_at >= COALESCE(doc_registry.verdict_computed_at, '')
        THEN EXCLUDED.pipeline_version
        ELSE doc_registry.pipeline_version
    END,
    permanent_marginal = CASE
        WHEN EXCLUDED.verdict_computed_at >= COALESCE(doc_registry.verdict_computed_at, '')
        THEN EXCLUDED.permanent_marginal
        ELSE doc_registry.permanent_marginal
    END,
    verdict_computed_at = CASE
        WHEN EXCLUDED.verdict_computed_at >= COALESCE(doc_registry.verdict_computed_at, '')
        THEN EXCLUDED.verdict_computed_at
        ELSE doc_registry.verdict_computed_at
    END;
"""


async def upsert_doc(meta: dict) -> None:
    """Insert or update a registry row from a metadata dict.

    Tolerates missing keys with safe defaults so it can be called directly
    with a raw ``.meta.json`` payload.  Returns silently if the pool is not
    initialised.
    """
    pool = _schema.get_pool()
    if pool is None:
        return
    doc_id = meta.get("doc_id", "")
    if not doc_id:
        logger.warning("registry.upsert_doc: skipping row with empty doc_id")
        return
    await pool.execute(
        _UPSERT_SQL,
        doc_id,
        meta.get("doc_name", ""),
        meta.get("source_url", ""),
        meta.get("processed_at", ""),
        meta.get("content_class", ""),
        meta.get("sha256", ""),
        meta.get("product", ""),
        meta.get("tier", ""),
        meta.get("doc_family", ""),
        meta.get("effective_date", ""),
        meta.get("doc_description", ""),
        # D2 (RFC-009): nullable node_count column. None when the caller has no
        # count (e.g. a raw legacy .meta.json) — read_registry_fields always
        # supplies it for freshly ingested docs.
        meta.get("node_count"),
        # RFC-014 D2: verdict columns for corpus promotion pipeline.
        meta.get("verdict", ""),
        meta.get("pipeline_version"),
        meta.get("permanent_marginal", False),
        # Zone-8: verdict_computed_at for temporal CAS guard.
        meta.get("verdict_computed_at", ""),
    )
    logger.debug("registry: upserted doc_id=%s", doc_id)


# ---------------------------------------------------------------------------
# Zone-4: verdict-only upsert with RETURNING (sole CAS-guarded verdict write)
# ---------------------------------------------------------------------------

_UPSERT_VERDICT_SQL = """
INSERT INTO doc_registry (
    doc_id, doc_name, source_url, processed_at,
    content_class, sha256, product, tier, doc_family,
    effective_date, doc_description, node_count,
    verdict, pipeline_version, permanent_marginal,
    verdict_computed_at
) VALUES ($1, '', '', '', '', '', '', '', '', '', '', NULL, $2, $3, $4, $5)
ON CONFLICT (doc_id) DO UPDATE SET
    -- Zone-8: temporal guard — same CAS logic as _UPSERT_SQL.
    verdict = CASE
        WHEN EXCLUDED.verdict_computed_at >= COALESCE(doc_registry.verdict_computed_at, '')
        THEN COALESCE(NULLIF(EXCLUDED.verdict, ''), doc_registry.verdict)
        ELSE doc_registry.verdict
    END,
    pipeline_version = CASE
        WHEN EXCLUDED.verdict_computed_at >= COALESCE(doc_registry.verdict_computed_at, '')
        THEN EXCLUDED.pipeline_version
        ELSE doc_registry.pipeline_version
    END,
    permanent_marginal = CASE
        WHEN EXCLUDED.verdict_computed_at >= COALESCE(doc_registry.verdict_computed_at, '')
        THEN EXCLUDED.permanent_marginal
        ELSE doc_registry.permanent_marginal
    END,
    verdict_computed_at = CASE
        WHEN EXCLUDED.verdict_computed_at >= COALESCE(doc_registry.verdict_computed_at, '')
        THEN EXCLUDED.verdict_computed_at
        ELSE doc_registry.verdict_computed_at
    END
RETURNING doc_id, verdict, pipeline_version, permanent_marginal, verdict_computed_at;
"""


async def upsert_verdict(doc_id: str, verdict_fields: dict[str, Any]) -> dict[str, Any] | None:
    """CAS-guarded verdict-only upsert with RETURNING.

    Writes *only* the four verdict columns (verdict, pipeline_version,
    permanent_marginal, verdict_computed_at) using the same temporal CAS guard
    as ``_UPSERT_SQL``.  Returns the winning row's verdict columns as a dict
    so the caller knows exactly which values landed in Postgres, regardless of
    whether the incoming write won or the existing row's values were preserved.

    Returns ``None`` when the pool is unavailable or ``doc_id`` is empty.
    Never raises — callers must handle ``None`` as "Postgres unavailable".

    This is the sole CAS-guarded verdict write entry point for the
    Postgres-authority (``REGISTRY_VERDICT_AUTHORITY=postgres``) path.
    """
    pool = _schema.get_pool()
    if pool is None:
        return None
    if not doc_id:
        logger.warning("registry.upsert_verdict: skipping empty doc_id")
        return None

    row = await pool.fetchrow(
        _UPSERT_VERDICT_SQL,
        doc_id,
        verdict_fields.get("verdict", ""),
        verdict_fields.get("pipeline_version"),
        verdict_fields.get("permanent_marginal", False),
        verdict_fields.get("verdict_computed_at", ""),
    )
    if row is None:
        return None
    result = dict(row)
    logger.debug(
        "registry: upsert_verdict doc_id=%s winning_verdict=%s",
        doc_id,
        result.get("verdict"),
    )
    return result


# ---------------------------------------------------------------------------
# Sweep path (RFC-014 D3 — version-gated backfill)
# ---------------------------------------------------------------------------

_SWEEP_CANDIDATES_SQL = """
SELECT doc_id FROM doc_registry
WHERE (pipeline_version IS NULL OR pipeline_version < $1)
  AND permanent_marginal = false;
"""


async def sweep_candidates(current_version: int) -> list[str]:
    """Return doc_ids eligible for verdict re-check (D3 sweep)."""
    pool = _schema.get_pool()
    if pool is None:
        return []
    rows = await pool.fetch(_SWEEP_CANDIDATES_SQL, current_version)
    return [r["doc_id"] for r in rows]


# ---------------------------------------------------------------------------
# Delete path (HR2 erasure cascade — step 6)
# ---------------------------------------------------------------------------

_DELETE_SQL = "DELETE FROM doc_registry WHERE doc_id = $1;"


async def delete_doc(doc_id: str) -> None:
    """Remove a doc_registry row.  Idempotent — no-op if the row is absent.
    Returns silently if the pool is not initialised.
    """
    pool = _schema.get_pool()
    if pool is None:
        return
    from ..config import settings

    await pool.execute(_DELETE_SQL, doc_id, timeout=settings.registry_delete_timeout_s)
    logger.info("registry: deleted doc_id=%s", doc_id)


_LIST_ALL_DOC_IDS_SQL = "SELECT doc_id FROM doc_registry;"


async def list_all_doc_ids() -> set[str] | None:
    """Return every doc_id currently in the registry (including verdict='FAIL'
    rows — deletion-drift reconciliation needs the true row set, not just the
    queryable subset). Returns ``None`` on any Postgres error so the caller can
    treat "unknown" distinctly from "empty" and skip a destructive sync.
    """
    pool = _schema.get_pool()
    if pool is None:
        return None
    try:
        rows = await pool.fetch(_LIST_ALL_DOC_IDS_SQL)
        return {r["doc_id"] for r in rows}
    except Exception as exc:
        logger.error("registry: list_all_doc_ids failed: %s", exc)
        return None


_LIST_ALL_DOC_IDS_WITH_TS_SQL = "SELECT doc_id, processed_at FROM doc_registry;"


async def list_all_doc_ids_with_timestamps() -> dict[str, str] | None:
    """Return ``{doc_id: processed_at}`` for every registry row.

    Zone-7: the processed_at timestamp lets _delete_stale_rows apply an
    age guard so freshly-ingested docs whose MinIO sidecar wasn't in the
    stale listing snapshot are not incorrectly deleted.

    Includes ALL rows regardless of verdict (matching list_all_doc_ids
    semantics — deletion-drift reconciliation needs the true row set).
    Returns ``None`` on any Postgres error so the caller can skip the
    destructive sync safely.
    """
    pool = _schema.get_pool()
    if pool is None:
        return None
    try:
        rows = await pool.fetch(_LIST_ALL_DOC_IDS_WITH_TS_SQL)
        return {r["doc_id"]: r["processed_at"] for r in rows}
    except Exception as exc:
        logger.error("registry: list_all_doc_ids_with_timestamps failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Read path — recent_documents (F5)
# ---------------------------------------------------------------------------

_LIST_SQL = """
SELECT doc_id, doc_name, source_url, processed_at, content_class, node_count
FROM   doc_registry
WHERE  verdict NOT IN ('FAIL', '')
ORDER  BY processed_at DESC
LIMIT  $1 OFFSET $2;
"""

_COUNT_SQL = "SELECT COUNT(*) FROM doc_registry WHERE verdict NOT IN ('FAIL', '');"

# Unfiltered row count — deliberately does NOT apply the verdict != 'FAIL'
# predicate. registry_backfill.py's empty-corpus guard (D3 / Property 7) needs
# the true row count to distinguish "Postgres is genuinely empty" from "every
# row is FAIL-verdict"; count_docs() alone can no longer answer that once it
# reflects only queryable (non-FAIL) rows (Phase 3 audit Issue B).
_COUNT_ALL_SQL = "SELECT COUNT(*) FROM doc_registry;"


async def list_docs(limit: int = 100, offset: int = 0) -> list[dict] | None:
    """Paginated listing, newest first.

    Returns a list of dicts with keys matching the legacy
    ``list_processed_docs()`` output so callers require no changes.
    Returns ``None`` on any Postgres error so the caller can fall back.
    """
    pool = _schema.get_pool()
    if pool is None:
        return None
    try:
        rows = await pool.fetch(_LIST_SQL, limit, offset)
        return [
            {
                "doc_id": r["doc_id"],
                "doc_name": r["doc_name"],
                "source_url": r["source_url"],
                "processed_at": r["processed_at"],
                "content_class": r["content_class"],
                # D2 (RFC-009): node_count surfaced so recent_documents (D3) can
                # paginate without deserializing trees. NULL for rows written
                # before the migration/backfill.
                "node_count": r["node_count"],
            }
            for r in rows
        ]
    except Exception as exc:
        logger.error("registry.list_docs failed: %s", exc)
        return None


async def count_docs() -> int | None:
    """Queryable row count (excludes verdict='FAIL' and verdict='' rows).  Returns None on error."""
    pool = _schema.get_pool()
    if pool is None:
        return None
    try:
        val = await pool.fetchval(_COUNT_SQL)
        return int(val)
    except Exception as exc:
        logger.error("registry.count_docs failed: %s", exc)
        return None


async def count_docs_all() -> int | None:
    """Total row count, including verdict='FAIL' rows.  Returns None on error.

    Used only by registry_backfill.py's empty-corpus guard, which needs the
    true row count rather than the queryable-only count (Phase 3 audit Issue B).
    """
    pool = _schema.get_pool()
    if pool is None:
        return None
    try:
        val = await pool.fetchval(_COUNT_ALL_SQL)
        return int(val)
    except Exception as exc:
        logger.error("registry.count_docs_all failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Read path — Stage B: lexical/BM25 narrowing (F7)
# ---------------------------------------------------------------------------

_STAGE_B_SQL = """
SELECT doc_id, doc_name, source_url, processed_at, content_class
FROM   doc_registry
WHERE  verdict NOT IN ('FAIL', '')
  AND  search_text @@ plainto_tsquery('simple', $1)
ORDER  BY ts_rank(search_text, plainto_tsquery('simple', $1)) DESC
LIMIT  $2;
"""

# Stage B full-scan fallback: when the query matches nothing via ts_rank we
# return the top-K most-recent docs so the LLM prefilter still has something to
# work with (mirrors the current behaviour of loading all docs).
_STAGE_B_FALLBACK_SQL = """
SELECT doc_id, doc_name, source_url, processed_at, content_class
FROM   doc_registry
WHERE  verdict NOT IN ('FAIL', '')
ORDER  BY processed_at DESC
LIMIT  $1;
"""


async def stage_b_candidates(query: str, topk: int) -> list[dict] | None:
    """BM25-style lexical narrowing via Postgres ts_rank/GIN (RFC-006 Stage B).

    Returns up to ``topk`` docs ranked by full-text relevance of ``search_text``
    against ``query``.  Falls back to the ``topk`` most-recent docs when the
    lexical query yields no results (so the LLM prefilter always gets a non-empty
    set for broad/vague queries).  Returns ``None`` on Postgres error so the
    caller can fall back to MinIO listing.
    """
    pool = _schema.get_pool()
    if pool is None:
        return None
    try:
        rows = await pool.fetch(_STAGE_B_SQL, query, topk)
        if not rows:
            # Broad/vague query — no lexical matches; fall through to recency sort.
            logger.info(
                "registry.stage_b_candidates: no ts_rank matches for query=%r; "
                "using recency-top-%d fallback",
                query[:80],
                topk,
            )
            rows = await pool.fetch(_STAGE_B_FALLBACK_SQL, topk)
        logger.info(
            "registry.stage_b_candidates: returning %d candidate(s) (topk=%d)",
            len(rows),
            topk,
        )
        return [
            {
                "doc_id": r["doc_id"],
                "doc_name": r["doc_name"],
                "source_url": r["source_url"],
                "processed_at": r["processed_at"],
                "content_class": r["content_class"],
            }
            for r in rows
        ]
    except Exception as exc:
        logger.error("registry.stage_b_candidates failed: %s", exc)
        return None


# ---------------------------------------------------------------------------
# Stage A: structured facet filter (RFC-006 F6)
# ---------------------------------------------------------------------------
# This is a no-op pass-through until the Tier-1 node-metadata fields land.
# When facets are populated, exact case-folded matching against known values
# is used — no substring/fuzzy match (grilled + locked 2026-07-03).
# The function signature is stable so callers need no changes when Tier-1 lands.

# Known facet values: populated at Tier-1 by whoever fills product/tier/doc_family.
# Keeping them as a module-level dict allows a future admin endpoint or startup
# query to refresh the set without restarting the process.
_KNOWN_FACETS: dict[str, set[str]] = {
    "product": set(),
    "tier": set(),
    "doc_family": set(),
}

_STAGE_A_SQL_TEMPLATE = """
SELECT doc_id, doc_name, source_url, processed_at, content_class
FROM   doc_registry
WHERE  {where_clause}
ORDER  BY processed_at DESC;
"""


async def stage_a_filter(
    query: str,
    facet_hints: dict[str, str] | None = None,
) -> list[dict] | None:
    """Stage A: exact, case-folded facet filter (RFC-006 F6).

    ``facet_hints`` is an optional caller-supplied override mapping
    facet column name → exact value (e.g. ``{"product": "huk-coburg"}``).
    When ``None``, the function attempts to resolve facet values from the
    query text against ``_KNOWN_FACETS``.

    Returns a filtered list when one or more facets match, otherwise ``None``
    (signals "fall through to Stage B" — never drops a candidate).
    Returns ``None`` also when Postgres is unavailable or Tier-1 facets are
    not yet populated (i.e., all ``_KNOWN_FACETS`` sets are empty), making
    this stage a transparent no-op.
    """
    pool = _schema.get_pool()
    if pool is None:
        return None

    # No facets populated yet (pre-Tier-1) → transparent pass-through.
    if not any(_KNOWN_FACETS.values()):
        return None

    resolved: dict[str, str] = {}

    if facet_hints:
        # Caller-supplied hints take priority; case-fold for safety.
        for col, val in facet_hints.items():
            if col in _KNOWN_FACETS and val.lower() in _KNOWN_FACETS[col]:
                resolved[col] = val.lower()
    else:
        # Auto-resolve: check each token in the query against known facet values.
        # Exact match only — never substring/fuzzy (locked 2026-07-03).
        q_lower = query.lower()
        for col, known in _KNOWN_FACETS.items():
            for val in known:
                # Word-boundary check: the token must appear as a standalone word.
                if f" {val} " in f" {q_lower} ":
                    resolved[col] = val
                    break  # one value per facet column per query

    if not resolved:
        return None  # no facet signal found → fall through to Stage B

    clauses = ["verdict NOT IN ('FAIL', '')"] + [
        f"{col} = ${i + 1}" for i, col in enumerate(resolved)
    ]
    sql = _STAGE_A_SQL_TEMPLATE.format(where_clause=" AND ".join(clauses))
    params = list(resolved.values())

    try:
        rows = await pool.fetch(sql, *params)
        logger.info(
            "registry.stage_a_filter: %d doc(s) matched facets %s",
            len(rows),
            resolved,
        )
        if not rows:
            # Facet matched known values but no docs carry those values yet.
            return None
        return [
            {
                "doc_id": r["doc_id"],
                "doc_name": r["doc_name"],
                "source_url": r["source_url"],
                "processed_at": r["processed_at"],
                "content_class": r["content_class"],
            }
            for r in rows
        ]
    except Exception as exc:
        logger.error("registry.stage_a_filter failed: %s", exc)
        return None


def refresh_known_facets(facets: dict[str, set[str]]) -> None:
    """Update the module-level known-facets lookup table.

    Called at startup (or by an admin endpoint) to populate the valid facet
    value sets from Postgres.  Until called, Stage A is a no-op.
    """
    for col, vals in facets.items():
        if col in _KNOWN_FACETS:
            _KNOWN_FACETS[col] = {v.lower() for v in vals}
    counts = {k: len(v) for k, v in _KNOWN_FACETS.items()}
    logger.info("registry: known_facets refreshed — %s", counts)


# ---------------------------------------------------------------------------
# Backfill flag helpers (RFC-006 F4)
# ---------------------------------------------------------------------------
# The registry_complete flag is stored in Redis (key: pageindex:registry:complete)
# so it survives worker restarts and is visible to both the MCP server and the
# arq worker without a Postgres query on every request.

_REGISTRY_COMPLETE_KEY = "pageindex:registry:complete"


async def set_registry_complete(redis_client: Any) -> None:
    """Mark backfill as complete in Redis.  Called by the backfill script."""
    await redis_client.set(_REGISTRY_COMPLETE_KEY, "1")
    logger.info("registry: backfill complete flag set in Redis")


async def is_registry_complete(redis_client: Any) -> bool:
    """Return True when the backfill complete flag is set in Redis."""
    try:
        val = await redis_client.get(_REGISTRY_COMPLETE_KEY)
        return val in (b"1", "1")
    except Exception as exc:
        logger.warning("registry.is_registry_complete: Redis error — %s", exc)
        return False
