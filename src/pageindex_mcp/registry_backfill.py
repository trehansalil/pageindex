"""RFC-006 F3 — One-time registry backfill script.

Walks MinIO ``processed/*.meta.json`` sidecars and upserts each into the
Postgres ``doc_registry`` table.  Sets the ``pageindex:registry:complete``
flag in Redis once every known doc is covered so the read paths in
``documents.py`` and ``helpers.py`` can switch over to the registry.

Usage::

    # Dry run (prints what would be upserted, makes no DB/Redis writes):
    uv run python -m pageindex_mcp.registry_backfill --dry-run

    # Live run (upserts + sets flag on success):
    uv run python -m pageindex_mcp.registry_backfill

    # Force re-run even if registry_complete flag is already set:
    uv run python -m pageindex_mcp.registry_backfill --force

Sequencing contract (RFC-006 F3):
  * Dual-write (save_doc_meta) ships FIRST so new docs written after the
    backfill starts are already in the registry.
  * This script backfills the existing corpus in a single pass.
  * Only after the pass completes without error does it set the Redis
    ``pageindex:registry:complete`` flag.
  * Until that flag is set, the read paths fall back to MinIO listing
    (REGISTRY_FALLBACK_TOTAL reason=backfill_incomplete) — no gap ever
    silently under-returns results (RFC-006 F4 / HR5 spirit).

Idempotent: ``upsert_doc`` is an ``INSERT … ON CONFLICT DO UPDATE`` so
running the script multiple times is safe.
"""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Bootstrap: ensure the src/ tree is on sys.path when run as a script.
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent  # …/pageindex/
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from pageindex_mcp.config import settings  # noqa: E402
from pageindex_mcp.registry import (  # noqa: E402
    close_registry,
    init_registry,
    is_registry_complete,
    set_registry_complete,
    upsert_doc,
)
from pageindex_mcp.storage import get_minio  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("registry_backfill")


# ---------------------------------------------------------------------------
# MinIO helpers
# ---------------------------------------------------------------------------


def _list_meta_keys() -> list[str]:
    """Return all ``processed/*.meta.json`` object keys from MinIO."""
    mc = get_minio()
    keys: list[str] = []
    for obj in mc.list_objects(settings.minio_bucket, prefix="processed/", recursive=True):
        name = obj.object_name or ""
        if name.endswith(".meta.json"):
            keys.append(name)
    return keys


def _load_meta(object_key: str) -> dict | None:
    """Fetch and parse a single .meta.json from MinIO.  Returns None on error."""
    mc = get_minio()
    response = None
    try:
        response = mc.get_object(settings.minio_bucket, object_key)
        return json.loads(response.read())
    except Exception as exc:
        logger.warning("Failed to load %s: %s", object_key, exc)
        return None
    finally:
        if response is not None:
            try:
                response.close()
                response.release_conn()
            except Exception:
                pass


# ---------------------------------------------------------------------------
# Main backfill coroutine
# ---------------------------------------------------------------------------


async def _preflight_checks() -> None:
    """Abort with sys.exit(1) when required env vars are missing."""
    if not settings.registry_enabled:
        logger.error(
            "REGISTRY_ENABLED=false — nothing to do. "
            "Set REGISTRY_ENABLED=true (or omit it) and re-run."
        )
        sys.exit(1)
    if not settings.postgres_dsn:
        logger.error(
            "POSTGRES_DSN is not set. "
            "Export it (e.g. postgresql://user:pass@localhost:5432/pageindex) and re-run."
        )
        sys.exit(1)


async def _upsert_all(meta_keys: list[str], dry_run: bool) -> list[str]:
    """Upsert every meta.json sidecar.  Returns a list of failed object keys."""
    failed: list[str] = []
    total = len(meta_keys)

    for i, key in enumerate(meta_keys, 1):
        meta = _load_meta(key)
        if meta is None:
            failed.append(key)
            continue

        doc_id = meta.get("doc_id", "")
        if not doc_id:
            stem = Path(key).stem  # e.g. "abc12345.meta"
            doc_id = stem.removesuffix(".meta")
            meta["doc_id"] = doc_id

        if dry_run:
            logger.info(
                "[DRY-RUN] %d/%d  would upsert doc_id=%s  doc_name=%r",
                i,
                total,
                doc_id,
                meta.get("doc_name", ""),
            )
            continue

        try:
            await upsert_doc(meta)
            if i % 50 == 0 or i == total:
                logger.info("Progress: %d/%d upserted …", i, total)
        except Exception as exc:
            logger.error("Failed to upsert doc_id=%s (%s): %s", doc_id, key, exc)
            failed.append(key)

    return failed


async def _backfill(dry_run: bool, force: bool) -> None:
    await _preflight_checks()

    if not dry_run:
        logger.info("Connecting to Postgres …")
        await init_registry(settings.postgres_dsn)  # type: ignore[arg-type]
        logger.info("Registry schema ready.")

    import redis.asyncio as aioredis

    redis_client = aioredis.from_url(settings.redis_url, decode_responses=False)

    if not force and not dry_run:
        already = await is_registry_complete(redis_client)
        if already:
            logger.info(
                "Registry complete flag is already set. Use --force to re-run the backfill anyway."
            )
            await redis_client.aclose()
            await close_registry()
            return

    logger.info("Listing processed/*.meta.json in MinIO bucket '%s' …", settings.minio_bucket)
    meta_keys = _list_meta_keys()
    logger.info("Found %d .meta.json sidecar(s).", len(meta_keys))

    if not meta_keys:
        # D3 / Property 7: zero keys means either an empty corpus or a
        # transient listing failure — never mark the registry complete on a
        # signal we can't distinguish from "backfill didn't actually run".
        logger.warning(
            "No .meta.json sidecars found — skipping backfill without marking complete."
        )
        await redis_client.aclose()
        if not dry_run:
            await close_registry()
        return

    failed = await _upsert_all(meta_keys, dry_run)
    ok = len(meta_keys) - len(failed)
    logger.info("Backfill complete: %d upserted, %d failed.", ok, len(failed))

    if failed:
        logger.error(
            "%d object(s) could not be upserted — registry_complete flag NOT set. "
            "Fix the errors above and re-run.\nFailed keys:\n  %s",
            len(failed),
            "\n  ".join(failed),
        )
        await redis_client.aclose()
        if not dry_run:
            await close_registry()
        sys.exit(1)

    if not dry_run:
        await set_registry_complete(redis_client)
        logger.info(
            "pageindex:registry:complete flag set in Redis. "
            "The read paths will now use the registry."
        )
    else:
        logger.info("[DRY-RUN] Would set pageindex:registry:complete in Redis.")

    await redis_client.aclose()
    if not dry_run:
        await close_registry()


# ---------------------------------------------------------------------------
# Entry point
# ---------------------------------------------------------------------------


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Backfill Postgres doc_registry from MinIO .meta.json sidecars."
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be upserted; make no DB or Redis writes.",
    )
    parser.add_argument(
        "--force",
        action="store_true",
        help="Re-run even if the registry_complete flag is already set.",
    )
    args = parser.parse_args()

    if args.dry_run:
        logger.info("DRY RUN — no writes will be made.")

    asyncio.run(_backfill(dry_run=args.dry_run, force=args.force))


if __name__ == "__main__":
    main()
