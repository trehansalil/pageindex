"""Cleanup submodule — stale row deletion."""

from __future__ import annotations

import logging
from datetime import UTC, datetime, timedelta

logger = logging.getLogger("registry_backfill")


def cleanup_protect_empty_processed_at(processed_at_str: str | None) -> bool:
    """Return True if a row with this *processed_at* value should be
    **treated as old enough** to be a stale-deletion candidate (i.e. NOT
    protected by the age guard).

    Rows with an empty or ``None`` ``processed_at`` (legacy rows that
    predate the timestamp column, or rows written with the schema default
    ``''``) are treated as old enough --- they are not protected by the
    age guard.

    Returns ``True`` (old enough, not protected) for empty/None values,
    ``False`` (possibly protected, needs further age check) otherwise.
    """
    return not processed_at_str

# A stale-row purge is only trusted when it wouldn't wipe out most of the
# registry — an untrustworthy/partial MinIO listing (e.g. list-API glitch,
# wrong bucket/prefix) should never cascade into mass deletion.
_MAX_STALE_DELETE_FRACTION = 0.5


async def _delete_stale_rows(
    minio_doc_ids: set[str],
    *,
    grace_minutes: int = 10,
) -> None:
    """Delete doc_registry rows whose MinIO .meta.json sidecar no longer
    exists, so a doc removed from MinIO (outside the HR2 erasure flow, e.g. a
    manual bucket cleanup) doesn't linger in listings/search indefinitely.

    Zone-7 age guard: rows whose ``processed_at`` is younger than
    *grace_minutes* are excluded from stale candidates.  This prevents
    the TOCTOU race where a document ingested *after* the MinIO listing
    snapshot was captured (but *before* this function runs) would be
    incorrectly deleted because its doc_id is present in Postgres but
    absent from the stale MinIO listing.

    Rows with an empty or unparseable ``processed_at`` (legacy rows that
    predate the timestamp, or rows written with the schema default ``''``)
    are treated as old enough to be stale candidates — they are not
    protected by the age guard.
    """
    from ..registry import delete_doc, list_all_doc_ids_with_timestamps

    registry_rows = await list_all_doc_ids_with_timestamps()
    if registry_rows is None:
        logger.warning(
            "reconcile_registry_drift: could not list registry doc_ids, skipping delete-drift check"
        )
        return

    registry_doc_ids = set(registry_rows)

    stale = registry_doc_ids - minio_doc_ids
    if not stale:
        return

    # Zone-7: filter out freshly-ingested rows protected by the age guard.
    cutoff = datetime.now(UTC) - timedelta(minutes=grace_minutes)
    age_protected: set[str] = set()
    for doc_id in stale:
        processed_at_str = registry_rows.get(doc_id, "")
        if cleanup_protect_empty_processed_at(processed_at_str):
            # Legacy row with empty/None processed_at — treat as old enough.
            continue
        try:
            processed_at = datetime.fromisoformat(processed_at_str)
            # Ensure timezone-aware comparison: if the stored timestamp has
            # no tzinfo, assume UTC (all current writers use UTC).
            if processed_at.tzinfo is None:
                processed_at = processed_at.replace(tzinfo=UTC)
            if processed_at >= cutoff:
                age_protected.add(doc_id)
        except (ValueError, TypeError):
            # Unparseable timestamp — treat as old enough to delete.
            logger.debug(
                "reconcile_registry_drift: unparseable processed_at for doc_id=%s: %r",
                doc_id,
                processed_at_str,
            )

    if age_protected:
        logger.info(
            "reconcile_registry_drift: excluding %d doc(s) from stale deletion "
            "(processed_at within %d-minute grace period)",
            len(age_protected),
            grace_minutes,
        )
    stale -= age_protected

    if not stale:
        return

    if len(stale) > len(registry_doc_ids) * _MAX_STALE_DELETE_FRACTION:
        logger.warning(
            "reconcile_registry_drift: refusing to delete %d/%d registry row(s) as stale "
            "(exceeds %.0f%% safety threshold) — check the MinIO listing before retrying",
            len(stale),
            len(registry_doc_ids),
            _MAX_STALE_DELETE_FRACTION * 100,
        )
        return

    deleted = 0
    for doc_id in stale:
        try:
            await delete_doc(doc_id)
            logger.debug("reconcile_registry_drift: deleted stale doc_id=%s", doc_id)
            deleted += 1
        except Exception as exc:
            logger.warning(
                "reconcile_registry_drift: failed to delete stale doc_id=%s: %s", doc_id, exc
            )
    logger.info("reconcile_registry_drift: deleted %d stale registry row(s)", deleted)
