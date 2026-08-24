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

import logging
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Bootstrap: ensure the src/ tree is on sys.path when run as a script.
# ---------------------------------------------------------------------------
_REPO_ROOT = Path(__file__).resolve().parent.parent.parent.parent  # …/pageindex/
_SRC = _REPO_ROOT / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s  %(levelname)-7s  %(message)s",
    datefmt="%H:%M:%S",
)

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
    _is_fat,
    _list_meta_entries,
    _list_meta_keys,
    _load_meta,
    _preflight_checks,
    _prepare_metas,
    _upsert_all,
    main,
    read_registry_fields,
    run_auto_backfill,
    upsert_doc,
)
from .cleanup import _delete_stale_rows  # noqa: E402
from .reconcile import (  # noqa: E402
    _drain_verdict_retry_queue,
    _record_reconcile_heartbeat,
    reconcile_registry_drift,
)

__all__ = [
    "_backfill",
    "_delete_stale_rows",
    "_drain_verdict_retry_queue",
    "_enrich_one",
    "_heal_orphans",
    "_is_fat",
    "_list_meta_entries",
    "_list_meta_keys",
    "_load_meta",
    "_preflight_checks",
    "_prepare_metas",
    "_record_reconcile_heartbeat",
    "_upsert_all",
    "main",
    "read_registry_fields",
    "reconcile_registry_drift",
    "run_auto_backfill",
    "upsert_doc",
]
