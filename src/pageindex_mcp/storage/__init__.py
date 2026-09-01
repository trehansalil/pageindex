"""storage package — re-exports public symbols for backward compatibility.

``from pageindex_mcp.storage import X`` continues to work after split.
"""

from __future__ import annotations

# Re-export config.settings so ``storage.settings`` remains patchable by tests.
from ..config import settings  # noqa: F401

# documents ────────────────────────────────────────────────────────────────────
from .documents import (
    delete_doc,
    get_flat_doc,
    load_doc,
    save_doc,
    save_figure,
    save_flat_doc,
    save_raw,
    wipe_processed,
)

# hash_cache ───────────────────────────────────────────────────────────────────
from .hash_cache import (
    HASH_CACHE_KEY,
    HASH_OBJECT,
    _load_legacy_minio_hash_cache,
    hash_cache_delete,
    hash_cache_get,
    hash_cache_set,
)

# minio_ops ────────────────────────────────────────────────────────────────────
from .minio_ops import (
    _WRITE_BARRIER_DELAYS,
    DEFAULT_PRESIGN_REGION,
    PersistenceNotVisibleError,
    _apply_route_prefix,
    _confirm_write_visible,
    _get_presign_minio,
    get_minio,
    presigned_get_url,
)

# reconcile_etag ───────────────────────────────────────────────────────────────
from .reconcile_etag import (
    RECONCILE_ETAG_KEY,
    reconcile_etag_delete,
    reconcile_etag_get_all,
    reconcile_etag_prune,
    reconcile_etag_set_many,
)

# staging ──────────────────────────────────────────────────────────────────────
from .staging import (
    delete_staging,
    download_staging,
    upload_staging,
)

# verdict ──────────────────────────────────────────────────────────────────────
from .verdict import (
    SIDECAR_VERSION,
    _read_existing_sidecar,
    list_processed_docs,
    read_registry_fields,
    save_doc_meta,
)

__all__ = [
    "DEFAULT_PRESIGN_REGION",
    "HASH_CACHE_KEY",
    "HASH_OBJECT",
    "RECONCILE_ETAG_KEY",
    "SIDECAR_VERSION",
    "_WRITE_BARRIER_DELAYS",
    "PersistenceNotVisibleError",
    "_apply_route_prefix",
    "_confirm_write_visible",
    "_get_presign_minio",
    "_load_legacy_minio_hash_cache",
    "_read_existing_sidecar",
    "delete_doc",
    "delete_staging",
    "download_staging",
    "get_flat_doc",
    "get_minio",
    "hash_cache_delete",
    "hash_cache_get",
    "hash_cache_set",
    "list_processed_docs",
    "load_doc",
    "presigned_get_url",
    "read_registry_fields",
    "reconcile_etag_delete",
    "reconcile_etag_get_all",
    "reconcile_etag_prune",
    "reconcile_etag_set_many",
    "save_doc",
    "save_doc_meta",
    "save_figure",
    "save_flat_doc",
    "save_raw",
    "upload_staging",
    "wipe_processed",
]
