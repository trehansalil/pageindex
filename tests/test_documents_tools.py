# tests/test_documents_tools.py
"""Regression tests for RFC-009 D1 (ISS-21): query-path not-found responses must
not trigger an O(N) MinIO listing.

RFC-009 Design Property 1 — "No O(N) listing on error paths": get_document,
get_document_structure, and get_page_content previously built an `available`
array via `list_processed_docs()` (a full MinIO listing) whenever a doc_id
was not found. Any invalid doc_id therefore had a DoS-shaped cost: a full
corpus listing on every miss. The fix drops the `available` enrichment and
returns a bare `{"error": ...}` body without touching storage at all.

These tests assert both halves of the contract:
  1. The JSON body is exactly {"error": "Document not found: <id>"} — no
     `available` key.
  2. `list_processed_docs` is never invoked on the not-found path.
"""

import json
from unittest.mock import AsyncMock, patch

from pageindex_mcp.tools import documents


def _not_found(_doc_id):
    raise KeyError("doc not found")


def test_get_document_not_found_no_listing():
    doc_id = "nonexistent-doc-id"
    with (
        patch("pageindex_mcp.tools.documents.get_doc", side_effect=_not_found),
        patch("pageindex_mcp.storage.list_processed_docs") as mock_list,
    ):
        result = documents.get_document(doc_id)

    assert json.loads(result) == {"error": f"Document not found: {doc_id}"}
    mock_list.assert_not_called()


def test_get_document_structure_not_found_no_listing():
    doc_id = "nonexistent-doc-id"
    with (
        patch("pageindex_mcp.tools.documents.get_doc", side_effect=_not_found),
        patch("pageindex_mcp.storage.list_processed_docs") as mock_list,
    ):
        result = documents.get_document_structure(doc_id)

    assert json.loads(result) == {"error": f"Document not found: {doc_id}"}
    mock_list.assert_not_called()


def test_get_page_content_not_found_no_listing():
    doc_id = "nonexistent-doc-id"
    with (
        patch("pageindex_mcp.tools.documents.get_doc", side_effect=_not_found),
        patch("pageindex_mcp.storage.list_processed_docs") as mock_list,
    ):
        result = documents.get_page_content(doc_id, "1-3")

    assert json.loads(result) == {"error": f"Document not found: {doc_id}"}
    mock_list.assert_not_called()


# ---------------------------------------------------------------------------
# Zone: Erasure Cascade / Storage Consistency — erasure-manifest guard tests
# ---------------------------------------------------------------------------

import logging

import pytest

from pageindex_mcp.storage.documents import (
    ErasureStep,
    _ERASURE_MANIFEST,
    _KNOWN_STORAGE_PREFIXES,
    _PREFIX_TO_ERASURE_STEPS,
    validate_erasure_manifest,
)


class TestValidateErasureManifest:
    """Contract tests for validate_erasure_manifest()."""

    def test_current_state_passes(self):
        """validate_erasure_manifest() must pass for the shipped code — every
        registered storage prefix has a corresponding ErasureStep."""
        # Should not raise
        validate_erasure_manifest()

    def test_raises_when_prefix_has_no_erasure_step(self):
        """Adding a registered prefix without a _PREFIX_TO_ERASURE_STEPS entry
        and without a matching ErasureStep must raise ImportError."""
        import pageindex_mcp.storage.documents as _docs_mod

        augmented = _KNOWN_STORAGE_PREFIXES | {"staging/"}
        with patch.object(_docs_mod, "_KNOWN_STORAGE_PREFIXES", augmented):
            with pytest.raises(ImportError, match="staging/"):
                validate_erasure_manifest()

    def test_raises_when_prefix_mapping_references_missing_step(self):
        """A _PREFIX_TO_ERASURE_STEPS entry that references a step name not in
        _ERASURE_MANIFEST must also be caught."""
        import pageindex_mcp.storage.documents as _docs_mod

        extra_prefixes = _KNOWN_STORAGE_PREFIXES | {"drafts/"}
        extra_mapping = dict(_PREFIX_TO_ERASURE_STEPS)
        extra_mapping["drafts/"] = ("drafts_cleanup",)
        with (
            patch.object(_docs_mod, "_KNOWN_STORAGE_PREFIXES", extra_prefixes),
            patch.object(_docs_mod, "_PREFIX_TO_ERASURE_STEPS", extra_mapping),
        ):
            with pytest.raises(ImportError, match="drafts_cleanup"):
                validate_erasure_manifest()

    def test_raises_when_consumer_precedes_producer(self):
        """RFC-043 D4: a step that consumes a ctx.* field must come after the
        step that produces it -- reordering must fail loudly at import time."""
        import pageindex_mcp.storage.documents as _docs_mod

        reordered = (
            ErasureStep(
                name="early_consumer",
                step=0,
                description="test",
                execute=AsyncMock(return_value=True),
                consumes=frozenset({"ctx.doc_name"}),
            ),
        ) + tuple(
            s for s in _ERASURE_MANIFEST if s.name != "early_consumer"
        )
        with patch.object(_docs_mod, "_ERASURE_MANIFEST", reordered):
            with pytest.raises(ValueError, match="early_consumer.*ctx.doc_name"):
                validate_erasure_manifest()

    def test_raises_when_reader_follows_deleter(self):
        """RFC-043 D4: a step that reads a sidecar must come before the step
        that deletes it -- reordering must fail loudly at import time."""
        import pageindex_mcp.storage.documents as _docs_mod

        by_name = {s.name: s for s in _ERASURE_MANIFEST}
        reordered = tuple(
            by_name["meta_json"] if name == "verdicts"
            else by_name["verdicts"] if name == "meta_json"
            else by_name[name]
            for name in by_name
        )
        with patch.object(_docs_mod, "_ERASURE_MANIFEST", reordered):
            with pytest.raises(
                ValueError, match="verdicts.*processed/\\{id\\}\\.meta\\.json"
            ):
                validate_erasure_manifest()


# ---------------------------------------------------------------------------
# Zone: Erasure Cascade / Storage Consistency — delete_doc log messages
# ---------------------------------------------------------------------------

from unittest.mock import MagicMock

from minio.error import S3Error


def _s3_no_such_key(*args, **kwargs):
    raise S3Error("NoSuchKey", "Not found", "", "", "", "")


def _s3_error(code):
    def _raise(*args, **kwargs):
        raise S3Error(code, "error", "", "", "", "")
    return _raise


def _mock_settings(**overrides):
    """Build a settings-like object with sensible defaults for erasure tests."""
    import dataclasses
    from pageindex_mcp.config import settings as _base
    return dataclasses.replace(_base, **overrides)


class TestDeleteDocLogMessages:
    """Regression: delete_doc log message must distinguish full success from
    partial-optional skip (ctx.errors empty but optional stores missed)."""

    @pytest.mark.asyncio
    async def test_full_success_logs_required_ok_and_optional_skipped(self, caplog):
        """When all required steps succeed and some optional steps are skipped
        (no errors), the log must report both counts, not just 'full cascade
        succeeded'."""
        from unittest.mock import AsyncMock

        from pageindex_mcp.storage.documents import delete_doc

        # Build a mock MinIO client that tolerates all remove/list calls.
        # get_object returns valid meta.json with no sha256 so verdicts step
        # is skipped (optional) but does not error.
        mock_mc = MagicMock()

        def _get_object_side_effect(bucket, key):
            if key.endswith(".meta.json"):
                # Return meta with no sha256 -> verdicts step returns False
                # but does not add an error (it logs a warning).
                resp = MagicMock()
                resp.read.return_value = b'{"content_class": "tree"}'
                return resp
            raise S3Error("NoSuchKey", "Not found", "", "", "", "")

        mock_mc.get_object.side_effect = _get_object_side_effect
        mock_mc.list_objects.return_value = iter([])
        mock_mc.remove_object.return_value = None

        # Provide a doc with doc_name so hash_cache and preloaded steps can
        # reach their stores (both tolerate NoSuchKey as success).
        mock_settings = _mock_settings(
            registry_enabled=True,
            postgres_dsn="postgresql://u:p@localhost/db",
        )

        with (
            patch("pageindex_mcp.storage.documents._minio_ops.get_minio", return_value=mock_mc),
            patch(
                "pageindex_mcp.storage.documents.load_doc",
                return_value={"doc_name": "test.pdf"},
            ),
            patch("pageindex_mcp.cache.doc_cache_delete", return_value=None),
            patch(
                "pageindex_mcp.storage.reconcile_etag.reconcile_etag_delete",
                return_value=None,
            ),
            patch(
                "pageindex_mcp.storage.hash_cache.hash_cache_delete",
                return_value=None,
            ),
            patch("pageindex_mcp.storage.documents.settings", mock_settings),
            patch("pageindex_mcp.registry.get_pool", return_value=object()),
            patch(
                "pageindex_mcp.registry.delete_doc",
                AsyncMock(return_value=None),
            ),
            caplog.at_level(logging.INFO, logger="pageindex_mcp.storage.documents"),
        ):
            result = await delete_doc("test-doc-1")

        assert result["errors"] == [], f"Expected no errors but got: {result['errors']}"
        # The log line must contain "cascade complete" with both counts
        cascade_msgs = [r.message for r in caplog.records if "cascade complete" in r.message]
        assert len(cascade_msgs) >= 1, (
            f"Expected 'cascade complete' log but got: "
            f"{[r.message for r in caplog.records]}"
        )
        msg = cascade_msgs[0]
        assert "required ok" in msg
        assert "optional skipped" in msg

    @pytest.mark.asyncio
    async def test_errors_logged_as_partial_failure(self, caplog):
        """When ctx.errors is non-empty, delete_doc logs 'partial failure'
        instead of 'cascade complete'."""
        from pageindex_mcp.storage.documents import delete_doc

        mock_mc = MagicMock()
        # uploads listing raises -> error
        mock_mc.list_objects.side_effect = _s3_error("InternalError")
        mock_mc.get_object.side_effect = _s3_no_such_key
        mock_mc.remove_object.return_value = None

        with (
            patch("pageindex_mcp.storage.documents._minio_ops.get_minio", return_value=mock_mc),
            patch("pageindex_mcp.storage.documents.load_doc", side_effect=ValueError("gone")),
            patch("pageindex_mcp.cache.doc_cache_delete", return_value=None),
            patch(
                "pageindex_mcp.storage.reconcile_etag.reconcile_etag_delete",
                return_value=None,
            ),
            patch(
                "pageindex_mcp.storage.documents.settings",
                _mock_settings(registry_enabled=False, postgres_dsn=""),
            ),
            caplog.at_level(logging.ERROR, logger="pageindex_mcp.storage.documents"),
        ):
            result = await delete_doc("test-doc-err")

        assert len(result["errors"]) > 0
        partial_msgs = [r.message for r in caplog.records if "partial failure" in r.message]
        assert len(partial_msgs) >= 1

    @pytest.mark.asyncio
    async def test_step1_failure_yields_partial_purge_true(self):
        """RFC-043 D5: when step 1 (uploads) fails, doc_name is never
        recovered, so the doc_name-dependent optional steps (hash_cache,
        preloaded) are skipped -- delete_doc must report partial_purge=True."""
        from pageindex_mcp.storage.documents import delete_doc

        mock_mc = MagicMock()
        # uploads listing raises -> step 1 fails, doc_name never recovered
        mock_mc.list_objects.side_effect = _s3_error("InternalError")
        mock_mc.get_object.side_effect = _s3_no_such_key
        mock_mc.remove_object.return_value = None

        with (
            patch("pageindex_mcp.storage.documents._minio_ops.get_minio", return_value=mock_mc),
            patch("pageindex_mcp.storage.documents.load_doc", side_effect=ValueError("gone")),
            patch("pageindex_mcp.cache.doc_cache_delete", return_value=None),
            patch(
                "pageindex_mcp.storage.reconcile_etag.reconcile_etag_delete",
                return_value=None,
            ),
            patch(
                "pageindex_mcp.storage.documents.settings",
                _mock_settings(registry_enabled=False, postgres_dsn=""),
            ),
        ):
            result = await delete_doc("test-doc-partial")

        assert result["partial_purge"] is True
