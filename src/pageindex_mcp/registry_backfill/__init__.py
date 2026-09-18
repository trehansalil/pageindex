"""RFC-006 F3 — Registry backfill package.

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

import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Bootstrap: ensure the src/ tree is on sys.path when run as a script.
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent  # …/pageindex/
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from pageindex_mcp.obs import configure as configure_obs  # noqa: E402

configure_obs()

from ..config import settings as settings  # noqa: E402
from ..storage import (  # noqa: E402
    reconcile_etag_get_all as reconcile_etag_get_all,
)
from ..storage import (  # noqa: E402
    reconcile_etag_prune as reconcile_etag_prune,
)
from ..storage import (  # noqa: E402
    reconcile_etag_set_many as reconcile_etag_set_many,
)
from .backfill import (  # noqa: E402
    _backfill,
    _enrich_one,
    _heal_orphans,
    _list_meta_entries,
    _list_meta_keys,
    _upsert_all,
    run_auto_backfill,
)
from .cleanup import (  # noqa: E402
    _delete_stale_rows,
    cleanup_protect_empty_processed_at,
)
from .reconcile import (  # noqa: E402
    _drain_verdict_retry_queue,
    reconcile_registry_drift,
)

__all__ = [
    "_backfill",
    "_delete_stale_rows",
    "cleanup_protect_empty_processed_at",
    "_drain_verdict_retry_queue",
    "_enrich_one",
    "_heal_orphans",
    "_list_meta_entries",
    "_list_meta_keys",
    "_upsert_all",
    "reconcile_registry_drift",
    "run_auto_backfill",
]
