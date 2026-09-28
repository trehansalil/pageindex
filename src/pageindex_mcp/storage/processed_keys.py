"""Which ``processed/`` objects are per-document sidecars, not documents.

Every lister of ``processed/`` derives a doc_id from the object name. The
RFC-052 table sidecar ``processed/<id>.tables.json`` also ends in ``.json``,
so a lister that does not skip it reads ``<id>.tables`` as a second document:
the registry reconcile healed it into ``<id>.tables.meta.json`` plus a bogus
``doc_registry`` row, and ``list_processed_docs`` listed the doc twice.
"""

TABLE_SIDECAR_SUFFIXES = (".tables.json", ".tables.meta.json")


def is_table_sidecar(object_name: str) -> bool:
    """True for a table sidecar, which never names a document of its own."""
    return object_name.endswith(TABLE_SIDECAR_SUFFIXES)
