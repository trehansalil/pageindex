"""
Version-gated corpus verdict sweep (RFC-014 D3).

Usage:
    python promotion_sweep.py

Queries doc_registry for rows with pipeline_version < CURRENT_PIPELINE_VERSION
and permanent_marginal = false, re-runs classify_verdict against each candidate's
stored tree JSON (no re-ingest, no re-extraction), and writes both sidecar and
registry updates.
"""

import asyncio
import json
import logging
from datetime import UTC, datetime

from pageindex_mcp.config import CURRENT_PIPELINE_VERSION, settings
from pageindex_mcp.helpers import (
    TreeDefect,
    TreeGateResult,
    _defect_from_reason_str,
    _tree_max_leaf_ratio,
    classify_verdict,
)
from pageindex_mcp.registry import (
    close_registry,
    init_registry,
    sweep_candidates,
    upsert_doc,
)
from pageindex_mcp.storage import get_minio, save_doc_meta, write_verdict

logger = logging.getLogger(__name__)


async def run_sweep() -> dict:
    """Execute the version-gated verdict sweep. Returns summary stats."""
    if not settings.postgres_dsn:
        logger.warning("Sweep: POSTGRES_DSN not configured, nothing to sweep")
        return {
            "candidates": 0,
            "updated": 0,
            "skipped": 0,
            "errors": 0,
            "pipeline_version": CURRENT_PIPELINE_VERSION,
        }

    await init_registry(settings.postgres_dsn)

    try:
        candidates = await sweep_candidates(CURRENT_PIPELINE_VERSION)
        logger.info(
            "Sweep: %d candidate(s) with pipeline_version < %d",
            len(candidates),
            CURRENT_PIPELINE_VERSION,
        )

        updated = 0
        skipped = 0
        errors = 0
        mc = get_minio()

        for doc_id in candidates:
            try:
                # Read stored processed doc JSON (read-only, no re-conversion)
                key = f"processed/{doc_id}.json"
                response = mc.get_object(settings.minio_bucket, key)
                try:
                    data = json.loads(response.read())
                finally:
                    response.close()
                    response.release_conn()

                structure = data.get("structure") or []
                content_class = data.get("content_class", "")

                existing_key = f"processed/{doc_id}.meta.json"
                stored_reason = None
                try:
                    resp = mc.get_object(settings.minio_bucket, existing_key)
                    try:
                        existing_meta = json.loads(resp.read())
                    finally:
                        resp.close()
                        resp.release_conn()
                    stored_reason = existing_meta.get("verdict_reason") or None
                except Exception:
                    pass

                # Zone-8 Target 7: parse stored verdict_reason via
                # TreeDefect enum to prevent sticky permanent FAILs from
                # prefix-matching on raw strings.
                validate_result = None
                if stored_reason:
                    defect = _defect_from_reason_str(stored_reason)
                    if defect != TreeDefect.OK:
                        validate_result = TreeGateResult(ok=False, defect=defect)
                verdict, verdict_reason = classify_verdict(
                    structure, content_class, validate_result
                )
                _, _, mlr = _tree_max_leaf_ratio(structure)

                verdict_computed_at = datetime.now(UTC).isoformat()

                # Zone-verdict-persistence: route verdict fields through
                # write_verdict (the sole verdict-mutation entry point) so
                # artifact and sidecar stay in sync.
                write_verdict(
                    doc_id,
                    verdict,
                    verdict_reason,
                    CURRENT_PIPELINE_VERSION,
                    verdict_computed_at,
                    mlr,
                    content_class=content_class or None,
                )

                # Non-verdict provenance through save_doc_meta (read-merge-write
                # preserves existing non-verdict fields without overwriting the
                # verdict fields just written by write_verdict).
                provenance_meta = {
                    "doc_id": doc_id,
                    "doc_name": data.get("doc_name", ""),
                    "source_url": data.get("source_url", ""),
                    "processed_at": data.get("processed_at", ""),
                }
                if content_class:
                    provenance_meta["content_class"] = content_class
                save_doc_meta(doc_id, provenance_meta)

                # Update registry with full metadata (including verdict fields
                # for the temporal CAS guard in the UPSERT SQL).
                registry_meta = {
                    "doc_id": doc_id,
                    "doc_name": data.get("doc_name", ""),
                    "source_url": data.get("source_url", ""),
                    "processed_at": data.get("processed_at", ""),
                    "verdict": verdict,
                    "verdict_reason": verdict_reason,
                    "max_leaf_ratio": round(mlr, 4),
                    "pipeline_version": CURRENT_PIPELINE_VERSION,
                    "verdict_computed_at": verdict_computed_at,
                }
                if content_class:
                    registry_meta["content_class"] = content_class
                await upsert_doc(registry_meta)

                updated += 1
                logger.info("Sweep: %s -> %s (%s)", doc_id, verdict, verdict_reason or "clean")

            except Exception as e:
                errors += 1
                logger.warning("Sweep: skipping %s: %s", doc_id, e)

        summary = {
            "candidates": len(candidates),
            "updated": updated,
            "skipped": skipped,
            "errors": errors,
            "pipeline_version": CURRENT_PIPELINE_VERSION,
        }
        logger.info("Sweep complete: %s", summary)
        return summary
    finally:
        await close_registry()


def main():
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(message)s")
    asyncio.run(run_sweep())


if __name__ == "__main__":
    main()
