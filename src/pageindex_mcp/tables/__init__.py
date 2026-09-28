"""RFC-052 P4: table capture, persistence and search (R7, R8).

``schema`` is the ``processed/<doc_id>.tables.json`` v1 contract; ``settings``
holds every ``TABLES_*`` knob; ``capture`` runs PyMuPDF ``find_tables()`` in a
spawn-context process pool beside the remote conversion (it never gates
dispatch, P6). Nothing here touches ``validate_tree`` or the quality gate (HR5).

HR4: the records are PyMuPDF (AGPL-3.0) output; the legal review is deferred
(user, 2026-09-27) and tracked as an open item in DESIGN.md.
"""
