"""Verdict persistence and sidecar read-merge-write."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from io import BytesIO
from pathlib import Path

from minio import Minio
from minio.error import S3Error

from ..config import settings
from ..metrics import (
    MINIO_DURATION,
    MINIO_OPS,
)
from . import minio_ops as _minio_ops

logger = logging.getLogger(__name__)

# C-3 sidecar v2 marker. Bumped from the implicit v1 (no marker) when the
# sidecar gained sha256 + doc_description + Tier-1 facets so the reconcile cron
# no longer has to GET the full processed JSON to enrich a registry row
# (audit Finding 9 / registry_backfill._bounded_enrich). The fat-vs-thin
# decision is made by FIELD PRESENCE (see registry_backfill._is_fat), never by
# this integer — the marker exists for telemetry/documentation only.
SIDECAR_VERSION = 4

# C-3: forward-compat Tier-1 facet fields. Omit-when-absent today (nobody
# generates them yet — C-1, P2), so writing them is a no-op until C-1 lands;
# listed here so the fat path stays lossless the moment they appear in `meta`.
_FACET_FIELDS = ("product", "tier", "doc_family", "effective_date")

_META_FIELDS = (
    "doc_id",
    "doc_name",
    "source_url",
    "processed_at",
    "sha256",
    "doc_description",
    "verdict",
    "verdict_reason",
    "max_leaf_ratio",
    "pipeline_version",
    "permanent_marginal",
    "promotion_eligible",
    "verdict_computed_at",
    "flat_char_count",
    "extraction_route",
    "converter_name",
    "converter_contract",
    "remote_build_sha",
    "build_sha",
    "page_count",
    "inspector_class",
    "total_tree_chars",
    *_FACET_FIELDS,
)


def _read_existing_sidecar(mc: Minio, doc_id: str) -> dict:
    """Best-effort read of the existing sidecar for merge semantics."""
    key = f"processed/{doc_id}.meta.json"
    try:
        response = mc.get_object(settings.minio_bucket, key)
        try:
            return json.loads(response.read())
        finally:
            response.close()
            response.release_conn()
    except Exception:
        return {}


def save_doc_meta(doc_id: str, meta: dict) -> None:  # noqa: C901, PLR0915
    """Read-merge-write sidecar: reads the existing sidecar (if any), merges
    new fields from *meta* on top, and writes the result.  This prevents
    subset-payload callers (promotion_sweep, registry_backfill) from
    accidentally dropping fields they don't carry.

    Zone-5 (verdict-persistence): this function is the **sole authoritative
    entry point** for verdict persistence.  Both the tree path
    (``_persist_tree_result``) and the flat path (``_persist_flat_result``)
    write verdict fields (verdict, verdict_reason, pipeline_version,
    verdict_computed_at, max_leaf_ratio) exclusively through this function.
    RFC-037 D5: the sidecar is a passive archive — Postgres ``_UPSERT_SQL``
    is the single max-priority-wins arbiter; callers pass the arbitrated
    ``RETURNING`` row, so the sidecar always reflects the Postgres value.
    New processed artifacts intentionally omit verdict fields from their
    body; ``read_registry_fields`` falls back to the sidecar to source them.

    Legacy callers (``promotion_sweep``, ``preprocess_client``) may still
    route through the deprecated ``write_verdict()`` wrapper, which
    delegates here.

    NOTE (RFC-006): the Postgres registry dual-write is NOT done here. This
    function is invoked from the ``pageindex`` fork inside the isolated
    ``converters_cli`` child subprocess, which never opens a registry pool -- so
    a dual-write here would always no-op. The registry upsert is instead done in
    the long-lived worker parent (``worker._upsert_registry_row``) after the
    child returns, where ``startup()`` has opened the pool. See
    ``read_registry_fields`` below for the field source.
    """
    MINIO_OPS.labels(operation="put").inc()
    start = time.monotonic()
    mc = _minio_ops.get_minio()
    try:
        existing = _read_existing_sidecar(mc, doc_id)

        _base_fields = ("doc_id", "doc_name", "source_url", "processed_at")
        sidecar = {k: existing.get(k, "") for k in _base_fields}
        for k in _base_fields:
            if k in meta:
                sidecar[k] = meta[k]

        if meta.get("content_class"):
            sidecar["content_class"] = meta["content_class"]
        elif "content_class" in existing:
            sidecar["content_class"] = existing["content_class"]

        node_count = meta.get("node_count")
        if node_count is None and "structure" in meta:
            from ..helpers import _tree_node_count  # lazy: avoid import cycle

            node_count = _tree_node_count(meta.get("structure") or [])
        if node_count is not None:
            sidecar["node_count"] = int(node_count)
        elif "node_count" in existing:
            sidecar["node_count"] = existing["node_count"]

        _MERGE_FIELDS = (
            "verdict",
            "verdict_reason",
            "max_leaf_ratio",
            "pipeline_version",
            "permanent_marginal",
            "promotion_eligible",
            "verdict_computed_at",
            "flat_char_count",
            "extraction_route",
            "converter_name",
            "converter_contract",
            "remote_build_sha",
            "page_count",
            "inspector_class",
            "total_tree_chars",
            "sha256",
            "doc_description",
            "build_sha",
            "effective_config",
            "effective_config_at_job_start",
            "extraction_stages",
        )
        for f in _MERGE_FIELDS:
            if f in meta:
                sidecar[f] = meta[f]
            elif f in existing:
                sidecar[f] = existing[f]

        for ff in _FACET_FIELDS:
            if ff in meta:
                sidecar[ff] = meta[ff]
            elif ff in existing:
                sidecar[ff] = existing[ff]

        sidecar["sidecar_version"] = SIDECAR_VERSION
        content = json.dumps(sidecar, indent=2).encode()
        key = f"processed/{doc_id}.meta.json"
        mc.put_object(
            settings.minio_bucket,
            key,
            BytesIO(content),
            len(content),
            content_type="application/json",
        )
        # Zone-4 Phase 3: write-visibility barrier removed.  Postgres is the
        # sole verdict authority; the sidecar is archival-only, so no
        # read-after-write confirmation is needed.  The barrier in save_doc /
        # save_flat_doc (primary processed artifacts) is intentionally retained.
        logger.debug("Saved meta for doc %s (%d bytes)", doc_id, len(content))
    finally:
        MINIO_DURATION.labels(operation="put").observe(time.monotonic() - start)


def write_verdict(  # noqa: PLR0913
    doc_id: str,
    verdict: str,
    verdict_reason: str,
    pipeline_version: int,
    verdict_computed_at: str,
    max_leaf_ratio: float,
    content_class: str | None = None,
) -> None:
    """**Deprecated (Zone-5):** thin wrapper that delegates to ``save_doc_meta``.

    Retained only for legacy callers (``promotion_sweep.run_sweep``,
    ``preprocess_client.recompute_verdicts``) that import and call this
    function directly.  New code should call ``save_doc_meta`` with the
    verdict fields in the *meta* dict instead.

    Previously this function performed an atomic dual-write of verdict
    fields to both the processed artifact and the sidecar.  Zone-5
    eliminated the artifact re-write; the sidecar (.meta.json) written by
    ``save_doc_meta`` is now the sole authoritative verdict store.
    """
    meta: dict = {
        "doc_id": doc_id,
        "verdict": verdict,
        "verdict_reason": verdict_reason,
        "pipeline_version": pipeline_version,
        "verdict_computed_at": verdict_computed_at,
        "max_leaf_ratio": round(max_leaf_ratio, 4),
    }
    if content_class:
        meta["content_class"] = content_class
    save_doc_meta(doc_id, meta)


# RFC-006: the registry needs richer fields (sha256, doc_description) than the
# lean .meta.json sidecar carries. Those live in the full processed-doc JSON, so
# the parent-side dual-write reads them from there.
_REGISTRY_FIELDS = (
    "doc_id",
    "doc_name",
    "source_url",
    "processed_at",
    "sha256",
    "doc_description",
    "product",
    "tier",
    "doc_family",
    "effective_date",
)


def read_registry_fields(doc_id: str, content_class: str | None = None) -> dict | None:
    """Return only the registry-relevant fields from a persisted processed doc.

    Reads ``processed/<id>.flat.json`` for flat docs (``content_class`` set) or
    ``processed/<id>.json`` for tree docs, and projects out just the columns the
    Postgres registry stores — the (potentially large) ``structure`` is parsed
    but discarded. Returns ``None`` if the object is missing or unreadable so
    the worker can skip the dual-write without failing the job.
    """
    key = f"processed/{doc_id}.flat.json" if content_class else f"processed/{doc_id}.json"
    MINIO_OPS.labels(operation="get").inc()
    start = time.monotonic()
    mc = _minio_ops.get_minio()
    response = None
    try:
        response = mc.get_object(settings.minio_bucket, key)
        data = json.loads(response.read())
        fields = {k: data.get(k, "") for k in _REGISTRY_FIELDS}
        fields["doc_id"] = doc_id
        if content_class:
            # Prefer the doc's own content_class (present in the .flat.json body)
            # over the passed marker so a reconcile orphan-heal — which only knows
            # "this is a flat doc" from the listing — still records the true value.
            fields["content_class"] = data.get("content_class") or content_class
        # D2 (RFC-009 / ISS-05): compute node_count from the tree structure here —
        # the processed doc is already loaded, so this is free — and dual-write it
        # into the registry's node_count column. Flat docs have no tree → 0.
        from ..helpers import _tree_node_count  # lazy: avoid import cycle

        fields["node_count"] = _tree_node_count(data.get("structure") or [])
        # RFC-014 D2 / Zone-8: carry verdict fields to registry.
        # Zone-5 NOTE: new artifacts (tree and flat) no longer embed verdict
        # fields in the artifact body -- the sidecar (.meta.json) is the
        # sole authoritative verdict store.  The artifact-body scan below
        # still fires for pre-Zone-5 legacy artifacts, but new docs will
        # always fall through to the sidecar-fallback path.
        _verdict_keys = (
            "verdict",
            "verdict_reason",
            "pipeline_version",
            "permanent_marginal",
            "verdict_computed_at",
            "max_leaf_ratio",
        )
        for vf in _verdict_keys:
            if vf in data and data[vf] not in (None, ""):
                fields[vf] = data[vf]
        # Zone-8 Target 4 / Zone-5: sidecar fallback.  For pre-Zone-5
        # legacy docs this was a defensive fallback; for Zone-5+ docs this
        # is now the primary verdict source (artifact body lacks verdict).
        if not fields.get("verdict"):
            sidecar = _read_existing_sidecar(mc, doc_id)
            if sidecar.get("verdict"):
                for vf in _verdict_keys:
                    if vf in sidecar and not fields.get(vf):
                        fields[vf] = sidecar[vf]
        return fields
    except S3Error as e:
        logger.warning("read_registry_fields: %s not readable (%s)", key, e.code)
        return None
    except Exception as e:
        logger.warning("read_registry_fields: failed for %s: %s", doc_id, e)
        return None
    finally:
        MINIO_DURATION.labels(operation="get").observe(time.monotonic() - start)
        if response is not None:
            try:
                response.close()
                response.release_conn()
            except Exception:
                pass


def list_processed_docs() -> list[dict]:
    """List all processed documents.  Reads lightweight .meta.json sidecars
    when available, falling back to full .json for legacy documents."""
    MINIO_OPS.labels(operation="list").inc()
    start = time.monotonic()
    mc = _minio_ops.get_minio()
    try:
        meta_keys: dict[str, str] = {}  # doc_id -> object_name (prefer .meta.json)
        for obj in mc.list_objects(settings.minio_bucket, prefix="processed/", recursive=True):
            name = obj.object_name
            if name.endswith(".meta.json"):
                doc_id = Path(name).stem.removesuffix(".meta")
                meta_keys[doc_id] = name
            elif name.endswith(".flat.json"):
                # Flat doc (FLAT-02-C3); prefer its .meta.json sidecar if present.
                doc_id = Path(name).stem.removesuffix(".flat")
                if doc_id not in meta_keys:
                    meta_keys[doc_id] = name
            elif name.endswith(".json") and not Path(name).name.startswith("_"):
                doc_id = Path(name).stem
                if doc_id not in meta_keys:
                    meta_keys[doc_id] = name

        def _fetch_one(doc_id: str, obj_name: str) -> dict | None:
            """Blocking single-doc fetch; run inside asyncio.to_thread by the
            bounded-concurrency fan-out below. Returns None on failure (logged)."""
            response = None
            try:
                response = mc.get_object(settings.minio_bucket, obj_name)
                data = json.loads(response.read())
                return {
                    "doc_id": data.get("doc_id", doc_id),
                    "doc_name": data.get("doc_name", data.get("filename", "unknown")),
                    "source_url": data.get("source_url", ""),
                    "processed_at": data.get("processed_at", ""),
                    "content_class": data.get("content_class", ""),
                    # D2 (RFC-009): node_count persisted at save time. Legacy
                    # sidecars predate this field — default to None (never
                    # KeyError) so recent_documents degrades gracefully.
                    "node_count": data.get("node_count"),
                }
            except Exception as e:
                logger.warning("Failed to read doc metadata %s: %s", obj_name, e)
                return None
            finally:
                if response is not None:
                    try:
                        response.close()
                        response.release_conn()
                    except Exception:
                        pass

        # D4 (RFC-013 / ISS-05): bounded-concurrency fetch instead of a serial
        # per-doc loop. mc.get_object is sync (minio.Minio), so each fetch is
        # offloaded to a worker thread via asyncio.to_thread; a semaphore caps
        # in-flight requests at 10 to avoid overwhelming MinIO on large corpora.
        async def _fetch_all() -> list[dict | None]:
            semaphore = asyncio.Semaphore(10)

            async def _bounded_fetch(doc_id: str, obj_name: str) -> dict | None:
                async with semaphore:
                    return await asyncio.to_thread(_fetch_one, doc_id, obj_name)

            tasks = [_bounded_fetch(doc_id, obj_name) for doc_id, obj_name in meta_keys.items()]
            return await asyncio.gather(*tasks, return_exceptions=True)

        # list_processed_docs is sync; callers on an async path (e.g.
        # client.py) already offload it via asyncio.to_thread, so this
        # function never runs on a thread that already owns a running loop.
        results = asyncio.run(_fetch_all())

        docs = [r for r in results if isinstance(r, dict)]
        logger.debug("Listed %d processed documents", len(docs))
        return docs
    finally:
        MINIO_DURATION.labels(operation="list").observe(time.monotonic() - start)
