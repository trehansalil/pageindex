"""Postgres document registry — schema, DDL, and pool lifecycle (RFC-006).

Provides a queryable catalog that replaces the O(N) MinIO listing path.
All public coroutines are safe to call when Postgres is unavailable — they
return ``None`` / empty lists so callers can fall back to MinIO silently (but
they must emit the REGISTRY_FALLBACK_TOTAL metric and a log warning, which is
the callers' responsibility as per RFC-006 F4).

Schema (``doc_registry`` table):
    doc_id          TEXT PRIMARY KEY          — 8-char UUID prefix
    doc_name        TEXT NOT NULL             — human-readable filename
    source_url      TEXT NOT NULL DEFAULT ''  — origin URL if known
    processed_at    TEXT NOT NULL DEFAULT ''  — ISO-8601 string from meta.json
    content_class   TEXT NOT NULL DEFAULT ''  — '' | 'flat_table' | …
    sha256          TEXT NOT NULL DEFAULT ''  — file hash from hash-cache
    product         TEXT NOT NULL DEFAULT ''  — Tier-1 facet (no-op until Tier-1 lands)
    tier            TEXT NOT NULL DEFAULT ''  — Tier-1 facet
    doc_family      TEXT NOT NULL DEFAULT ''  — Tier-1 facet
    effective_date  TEXT NOT NULL DEFAULT ''  — Tier-1 facet
    search_text     tsvector GENERATED ALWAYS AS (
                        to_tsvector('simple',
                            coalesce(doc_name,'') || ' ' ||
                            coalesce(doc_description,'') || ' ' ||
                            coalesce(product,'') || ' ' ||
                            coalesce(tier,'') || ' ' ||
                            coalesce(doc_family,''))
                        ) STORED
    doc_description TEXT NOT NULL DEFAULT ''  — LLM-generated description from meta

GIN index on ``search_text`` enables Stage B ``ts_rank`` queries.
``processed_at`` index enables ``recent_documents`` ``ORDER BY processed_at DESC``.

Dependency: asyncpg (PostgreSQL-licensed). Added to pyproject.toml under the
``[project.dependencies]`` section together with this file.
"""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    import asyncpg

logger = logging.getLogger(__name__)

# Module-level pool: initialised lazily by init_registry() and reused across
# requests. ``None`` when Postgres is unavailable or REGISTRY_ENABLED=False.
_pool: asyncpg.Pool | None = None

# ---------------------------------------------------------------------------
# DDL
# ---------------------------------------------------------------------------

_CREATE_TABLE_SQL = """
CREATE TABLE IF NOT EXISTS doc_registry (
    doc_id          TEXT        PRIMARY KEY,
    doc_name        TEXT        NOT NULL DEFAULT '',
    source_url      TEXT        NOT NULL DEFAULT '',
    processed_at    TEXT        NOT NULL DEFAULT '',
    content_class   TEXT        NOT NULL DEFAULT '',
    sha256          TEXT        NOT NULL DEFAULT '',
    product         TEXT        NOT NULL DEFAULT '',
    tier            TEXT        NOT NULL DEFAULT '',
    doc_family      TEXT        NOT NULL DEFAULT '',
    effective_date  TEXT        NOT NULL DEFAULT '',
    doc_description TEXT        NOT NULL DEFAULT '',
    node_count      INTEGER,
    search_text     tsvector    GENERATED ALWAYS AS (
        to_tsvector('simple',
            coalesce(doc_name, '') || ' ' ||
            coalesce(doc_description, '') || ' ' ||
            coalesce(product, '') || ' ' ||
            coalesce(tier, '') || ' ' ||
            coalesce(doc_family, ''))
    ) STORED
);
"""

_CREATE_GIN_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS doc_registry_search_gin
    ON doc_registry USING GIN (search_text);
"""

_CREATE_TIME_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS doc_registry_processed_at_idx
    ON doc_registry (processed_at DESC);
"""

# D2 (RFC-009 / ISS-05): additive column for node_count. Follows this repo's
# raw-DDL bootstrap convention (no Alembic) — an idempotent ADD COLUMN IF NOT
# EXISTS run at init so pre-existing deployments migrate in place. Nullable:
# rows written before this change stay NULL until the RFC-006 D3 backfill
# re-generates them.
_MIGRATE_NODE_COUNT_SQL = """
ALTER TABLE doc_registry ADD COLUMN IF NOT EXISTS node_count INTEGER;
"""

# RFC-014 D2: verdict columns for corpus promotion pipeline. Same
# idempotent ADD COLUMN IF NOT EXISTS pattern as node_count above.
_MIGRATE_VERDICT_SQL = """
ALTER TABLE doc_registry ADD COLUMN IF NOT EXISTS verdict TEXT NOT NULL DEFAULT '';
ALTER TABLE doc_registry ADD COLUMN IF NOT EXISTS pipeline_version INTEGER;
ALTER TABLE doc_registry ADD COLUMN IF NOT EXISTS permanent_marginal BOOLEAN NOT NULL DEFAULT false;
"""

# Zone-8: verdict_computed_at column for temporal CAS guard in upsert.
_MIGRATE_VERDICT_COMPUTED_AT_SQL = """
ALTER TABLE doc_registry ADD COLUMN IF NOT EXISTS verdict_computed_at TEXT NOT NULL DEFAULT '';
"""

_CREATE_VERDICT_INDEX_SQL = """
CREATE INDEX IF NOT EXISTS doc_registry_verdict_idx
    ON doc_registry (verdict, pipeline_version);
"""

# ---------------------------------------------------------------------------
# Pool lifecycle
# ---------------------------------------------------------------------------


async def init_registry(dsn: str) -> None:
    """Create the asyncpg connection pool and ensure the schema exists.

    Safe to call multiple times — idempotent via ``IF NOT EXISTS`` DDL and a
    module-level guard.  Raises on connection failure so the caller can decide
    whether to crash-start or degrade gracefully.
    """
    global _pool
    if _pool is not None:
        return  # already initialised

    import asyncpg  # deferred: server loads even without asyncpg in the env

    logger.info("registry: connecting to Postgres (DSN omitted for security)")
    _pool = await asyncpg.create_pool(dsn, min_size=1, max_size=10)
    async with _pool.acquire() as conn:
        await conn.execute(_CREATE_TABLE_SQL)
        await conn.execute(_MIGRATE_NODE_COUNT_SQL)
        await conn.execute(_MIGRATE_VERDICT_SQL)
        await conn.execute(_MIGRATE_VERDICT_COMPUTED_AT_SQL)
        await conn.execute(_CREATE_GIN_INDEX_SQL)
        await conn.execute(_CREATE_TIME_INDEX_SQL)
        await conn.execute(_CREATE_VERDICT_INDEX_SQL)
    logger.info("registry: schema ready (doc_registry)")


async def close_registry() -> None:
    """Drain and close the pool on shutdown."""
    global _pool
    if _pool is not None:
        await _pool.close()
        _pool = None
        logger.info("registry: pool closed")


def get_pool() -> asyncpg.Pool | None:
    """Return the module-level pool, or None if not initialised."""
    return _pool
