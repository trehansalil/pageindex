"""Verdict persistence, sidecar read-merge-write, and verdict ledger."""

from __future__ import annotations

import asyncio
import json
import logging
import time
from datetime import UTC, datetime
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


# Zone-8: verdict fields guarded by temporal CAS in save_doc_meta.
_VERDICT_CAS_FIELDS = frozenset(
    {
        "verdict",
        "verdict_reason",
        "pipeline_version",
        "verdict_computed_at",
        "max_leaf_ratio",
    }
)


def _verdict_cas_guard(existing: dict, incoming: dict) -> bool:
    """Soft CAS guard: return True when existing verdict is newer than incoming.

    Compares ``existing.get('verdict_computed_at')`` vs
    ``incoming.get('verdict_computed_at')``; if existing is strictly newer
    (lexicographic ISO-8601 comparison), the caller should skip verdict
    fields in the merge.  Only verdict/verdict_reason/pipeline_version/
    verdict_computed_at/max_leaf_ratio get the temporal guard -- all
    other fields are merged unconditionally.

    Returns False (allow the write) when either timestamp is absent so
    existing rows with NULL/missing verdict_computed_at always accept any
    incoming verdict.
    """
    existing_ts = existing.get("verdict_computed_at", "")
    incoming_ts = incoming.get("verdict_computed_at", "")
    if not existing_ts or not incoming_ts:
        return False  # no timestamp to compare -- allow the write
    if existing_ts > incoming_ts:
        logger.warning(
            "_verdict_cas_guard: existing verdict_computed_at=%s > incoming=%s; "
            "skipping verdict field merge for doc_id=%s",
            existing_ts,
            incoming_ts,
            incoming.get("doc_id", "?"),
        )
        return True
    return False


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
    The ``_verdict_cas_guard`` protects against out-of-order / lost-update
    verdict writes.  New processed artifacts intentionally omit verdict
    fields from their body; ``read_registry_fields`` falls back to the
    sidecar to source them.

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
        # Zone-8: soft CAS guard -- skip verdict fields when existing is newer
        _skip_verdict = _verdict_cas_guard(existing, meta)
        for f in _MERGE_FIELDS:
            if _skip_verdict and f in _VERDICT_CAS_FIELDS:
                # CAS guard fired -- preserve existing verdict fields
                if f in existing:
                    sidecar[f] = existing[f]
                continue
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
        # Zone-4: under Postgres-authority mode the sidecar is a best-effort
        # backfill, not the primary read path, so the RFC-034 D18 barrier is
        # unnecessary and its latency cost is avoided.  Under MinIO-authority
        # (default) the barrier remains to preserve the existing contract.
        if settings.registry_verdict_authority != "postgres":
            _minio_ops._confirm_write_visible(mc, settings.minio_bucket, key)
        logger.debug("Saved meta for doc %s (%d bytes)", doc_id, len(content))

        # Zone-4: persist verdict ledger entry (fire-and-forget).
        # Only write when the CAS guard did not skip verdict fields and
        # both verdict and sha256 are present in the merged sidecar.
        if not _skip_verdict and sidecar.get("verdict") and sidecar.get("sha256"):
            try:
                persist_verdict_ledger(
                    sidecar["sha256"],
                    sidecar["verdict"],
                    sidecar.get("verdict_reason", ""),
                )
            except Exception:
                logger.warning(
                    "save_doc_meta: verdict ledger write failed for doc %s, "
                    "continuing (fire-and-forget)",
                    doc_id,
                    exc_info=True,
                )
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


# ---------------------------------------------------------------------------
# Zone-4: deterministic verdict ledger (replaces dead hysteresis mechanism)
# ---------------------------------------------------------------------------
# Persisted at verdicts/{sha256}.json in MinIO -- a prefix outside processed/
# so it survives wipe_processed() and anchors verdict stability across
# reingestion cycles.  Max-priority-wins guard: an existing PASS is never
# downgraded to MARGINAL by a re-ingestion that computes a worse verdict.
#
# Replaces: find_prior_verdict, snapshot_prior_verdicts, _PRIOR_VERDICTS_KEY,
# _VERDICT_PRIORITY (RFC-025 D0 / RFC-026 D3 / RFC-033 D0 -- all dead code
# with zero production callers).

_LEDGER_VERDICT_PRIORITY = {"PASS": 3, "MARGINAL": 2, "FAIL": 1, "ERROR": 0}


def persist_verdict_ledger(sha256: str, verdict: str, reason: str) -> None:
    """Write or upgrade the per-content verdict ledger entry.

    Max-priority-wins guard: if ``verdicts/{sha256}.json`` already exists
    and records a higher-priority verdict (PASS > MARGINAL > FAIL > ERROR),
    the write is skipped.  This prevents a noisy re-ingestion from
    downgrading a previously stable verdict.

    Fire-and-forget: logs a warning on MinIO unavailability but never
    raises -- the ledger is a quality-of-life anchor, never a blocker.
    """
    key = f"verdicts/{sha256}.json"
    try:
        mc = _minio_ops.get_minio()
    except Exception:
        logger.warning("persist_verdict_ledger: MinIO unavailable, skipping")
        return
    try:
        # Read existing ledger entry for max-priority-wins guard
        response = None
        try:
            response = mc.get_object(settings.minio_bucket, key)
            existing = json.loads(response.read())
        except S3Error as exc:
            if exc.code == "NoSuchKey":
                existing = None
            else:
                raise
        except Exception:
            existing = None
        finally:
            if response is not None:
                try:
                    response.close()
                    response.release_conn()
                except Exception:
                    pass

        # Max-priority-wins guard
        if existing is not None:
            existing_verdict = existing.get("verdict", "")
            existing_priority = _LEDGER_VERDICT_PRIORITY.get(existing_verdict, -1)
            incoming_priority = _LEDGER_VERDICT_PRIORITY.get(verdict, -1)
            if existing_priority >= incoming_priority:
                logger.debug(
                    "persist_verdict_ledger: existing verdict %s (priority %d) >= "
                    "incoming %s (priority %d) for sha256=%s; skipping write",
                    existing_verdict,
                    existing_priority,
                    verdict,
                    incoming_priority,
                    sha256[:12],
                )
                return

        payload = json.dumps(
            {
                "sha256": sha256,
                "verdict": verdict,
                "verdict_reason": reason,
                "written_at": datetime.now(UTC).isoformat(),
            }
        ).encode("utf-8")
        mc.put_object(
            settings.minio_bucket,
            key,
            BytesIO(payload),
            length=len(payload),
            content_type="application/json",
        )
        logger.debug(
            "persist_verdict_ledger: wrote %s=%s for sha256=%s",
            verdict,
            reason,
            sha256[:12],
        )
    except Exception:
        logger.warning(
            "persist_verdict_ledger: failed for sha256=%s, skipping",
            sha256[:12],
            exc_info=True,
        )


def read_verdict_ledger(sha256: str) -> str | None:
    """Read the best-ever verdict from the per-content ledger.

    Returns the verdict string (PASS/MARGINAL/FAIL/ERROR) or None if no
    ledger entry exists or MinIO is unavailable.  Graceful degradation:
    hysteresis is a quality improvement, never a blocker.
    """
    key = f"verdicts/{sha256}.json"
    try:
        mc = _minio_ops.get_minio()
    except Exception:
        logger.warning("read_verdict_ledger: MinIO unavailable, skipping")
        return None
    response = None
    try:
        response = mc.get_object(settings.minio_bucket, key)
        entry = json.loads(response.read())
        return entry.get("verdict")
    except S3Error as exc:
        if exc.code == "NoSuchKey":
            return None
        logger.warning(
            "read_verdict_ledger: S3 error for sha256=%s (%s)",
            sha256[:12],
            exc,
        )
        return None
    except Exception:
        logger.warning(
            "read_verdict_ledger: failed for sha256=%s, skipping",
            sha256[:12],
            exc_info=True,
        )
        return None
    finally:
        if response is not None:
            try:
                response.close()
                response.release_conn()
            except Exception:
                pass
