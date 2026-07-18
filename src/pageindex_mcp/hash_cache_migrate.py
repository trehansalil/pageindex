"""RFC-007 D6 (Task 4.2) — One-time hash-cache migration script.

Reads the legacy ``hashes/processed_hashes.json`` MinIO blob, HSETs every
{filename: sha256} entry into the Redis hash ``pageindex:hashes``, then
deletes the MinIO blob.

Usage::

    # Dry run (prints what would be migrated, makes no Redis/MinIO writes):
    uv run python -m pageindex_mcp.hash_cache_migrate --dry-run

    # Live run (HSETs entries, then deletes the MinIO blob):
    uv run python -m pageindex_mcp.hash_cache_migrate

Belt-and-suspenders (RFC-007 D6): ``storage.hash_cache_get`` already falls
back to reading the legacy MinIO blob for any filename not yet found in
Redis, so this script is safe to run at any point during the deploy window.
Once it has run in every environment, that MinIO-fallback branch in
``storage.hash_cache_get`` may be removed after one full deploy cycle.

Idempotent: HSET overwrites are safe to repeat; deleting an already-deleted
MinIO blob is a no-op (NoSuchKey is tolerated).
"""

from __future__ import annotations

import argparse
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

from pageindex_mcp.cache import get_cache_redis  # noqa: E402
from pageindex_mcp.config import settings  # noqa: E402
from pageindex_mcp.storage import HASH_CACHE_KEY, HASH_OBJECT, get_minio  # noqa: E402

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("hash_cache_migrate")


def _load_legacy_blob() -> dict[str, str]:
    from minio.error import S3Error

    mc = get_minio()
    response = None
    try:
        response = mc.get_object(settings.minio_bucket, HASH_OBJECT)
        return json.loads(response.read())
    except S3Error as e:
        if e.code == "NoSuchKey":
            return {}
        raise
    finally:
        if response is not None:
            try:
                response.close()
                response.release_conn()
            except Exception:
                pass


def _delete_legacy_blob() -> None:
    from minio.error import S3Error

    mc = get_minio()
    try:
        mc.remove_object(settings.minio_bucket, HASH_OBJECT)
    except S3Error as e:
        if e.code != "NoSuchKey":
            raise


def migrate(dry_run: bool) -> int:
    cache = _load_legacy_blob()
    logger.info(
        "Found %d entr%s in legacy MinIO hash-cache blob.",
        len(cache),
        "y" if len(cache) == 1 else "ies",
    )

    if not cache:
        logger.info("Nothing to migrate.")
        return 0

    if dry_run:
        logger.info("DRY RUN — would HSET %d entries into %s.", len(cache), HASH_CACHE_KEY)
        return len(cache)

    r = get_cache_redis()
    r.hset(HASH_CACHE_KEY, mapping=cache)
    logger.info("Migrated %d entries into Redis HSET %s.", len(cache), HASH_CACHE_KEY)

    _delete_legacy_blob()
    logger.info("Deleted legacy MinIO blob %s.", HASH_OBJECT)
    return len(cache)


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Print what would be migrated; make no Redis/MinIO writes.",
    )
    args = parser.parse_args()

    if args.dry_run:
        logger.info("DRY RUN — no writes will be made.")

    migrate(dry_run=args.dry_run)


if __name__ == "__main__":
    main()
