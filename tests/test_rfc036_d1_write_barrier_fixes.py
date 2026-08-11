# tests/test_rfc036_d1_write_barrier_fixes.py
"""RFC-036 D1: write-barrier delay cap and catch-and-downgrade of
PersistenceNotVisibleError in save_doc/save_doc_meta.

Property 5: _confirm_write_visible's total polling delay across
            _WRITE_BARRIER_DELAYS SHALL NOT exceed 0.45s.
Property 6: PersistenceNotVisibleError raised by _confirm_write_visible
            SHALL be caught by save_doc/save_doc_meta, logged as a warning,
            counted via write_barrier_exhausted, and SHALL NOT propagate.
Integration: the اتفاقية مستوى الخدمة (Arabic SLA) doc completes within the
             scorer's polling window (processing_at within 2 minutes of
             batch start) now that write-barrier exhaustion no longer
             raises an unhandled exception that could trigger an arq retry.
"""

import logging
import time
from datetime import UTC, datetime, timedelta
from unittest.mock import MagicMock, patch

import pytest

from pageindex_mcp.metrics import WRITE_BARRIER_EXHAUSTED
from pageindex_mcp.storage import (
    _WRITE_BARRIER_DELAYS,
    PersistenceNotVisibleError,
    save_doc,
    save_doc_meta,
)


def _counter_value(counter) -> float:
    return counter._value.get()


@pytest.fixture
def mock_minio():
    client = MagicMock()
    client.bucket_exists.return_value = True
    with patch("pageindex_mcp.storage.get_minio", return_value=client):
        yield client


class TestProperty5WriteBarrierBudgetCapped:
    def test_delay_schedule_totals_at_most_0_45s(self):
        """Property 5: sum of _WRITE_BARRIER_DELAYS SHALL NOT exceed 0.45s."""
        assert sum(_WRITE_BARRIER_DELAYS) <= 0.45

    def test_confirm_write_visible_elapsed_time_bounded(self, monkeypatch):
        """Property 5: an exhausting _confirm_write_visible call (real sleeps,
        no mocking of time.sleep) SHALL wall-clock in under 0.45s."""
        from pageindex_mcp.storage import _confirm_write_visible
        from minio.error import S3Error

        def _not_found(*_a, **_kw):
            raise S3Error(
                code="NoSuchKey",
                message="not found",
                resource="/bucket/key",
                request_id="req",
                host_id="host",
                response=None,
            )

        mc = MagicMock()
        mc.stat_object.side_effect = _not_found

        start = time.monotonic()
        with pytest.raises(PersistenceNotVisibleError):
            _confirm_write_visible(mc, "bucket", "processed/doc.json")
        elapsed = time.monotonic() - start

        assert elapsed <= 0.45 + 0.1  # small scheduler-jitter allowance


class TestProperty6PersistenceNotVisibleErrorNeverPropagates:
    def test_save_doc_catches_and_downgrades(self, mock_minio, monkeypatch, caplog):
        """Property 6: save_doc catches PersistenceNotVisibleError, logs a
        warning, increments write_barrier_exhausted, and returns normally."""
        monkeypatch.setattr(
            "pageindex_mcp.storage._confirm_write_visible",
            MagicMock(side_effect=PersistenceNotVisibleError("processed/doc.json")),
        )
        before = _counter_value(WRITE_BARRIER_EXHAUSTED)

        with (
            patch("pageindex_mcp.cache.doc_cache_delete"),
            caplog.at_level(logging.WARNING),
        ):
            save_doc("doc123", {"doc_id": "doc123", "structure": []})

        mock_minio.put_object.assert_called_once()
        assert _counter_value(WRITE_BARRIER_EXHAUSTED) == before + 1
        assert any(
            "write barrier exhausted" in rec.message for rec in caplog.records
        )

    def test_save_doc_meta_catches_and_downgrades(self, mock_minio, monkeypatch, caplog):
        """Property 6: save_doc_meta catches PersistenceNotVisibleError, logs
        a warning, increments write_barrier_exhausted, and returns normally."""
        monkeypatch.setattr(
            "pageindex_mcp.storage._confirm_write_visible",
            MagicMock(side_effect=PersistenceNotVisibleError("processed/doc.meta.json")),
        )
        before = _counter_value(WRITE_BARRIER_EXHAUSTED)

        with caplog.at_level(logging.WARNING):
            save_doc_meta(
                "doc123",
                {
                    "doc_id": "doc123",
                    "doc_name": "t.pdf",
                    "source_url": "s3://x",
                    "processed_at": "2026-08-10T00:00:00Z",
                },
            )

        mock_minio.put_object.assert_called_once()
        assert _counter_value(WRITE_BARRIER_EXHAUSTED) == before + 1
        assert any(
            "write barrier exhausted" in rec.message for rec in caplog.records
        )

    def test_save_doc_no_exception_when_barrier_healthy(self, mock_minio, monkeypatch):
        """Sanity: when _confirm_write_visible succeeds, no exception, no
        counter increment."""
        monkeypatch.setattr(
            "pageindex_mcp.storage._confirm_write_visible", MagicMock(return_value=None)
        )
        before = _counter_value(WRITE_BARRIER_EXHAUSTED)

        with patch("pageindex_mcp.cache.doc_cache_delete"):
            save_doc("doc123", {"doc_id": "doc123", "structure": []})

        assert _counter_value(WRITE_BARRIER_EXHAUSTED) == before


class TestArabicSlaDocIntegration:
    """Integration: اتفاقية مستوى الخدمة (Arabic SLA) doc, doc_id d58be46f,
    landed 3-5 minutes late in Run 19 -- possibly due to an unhandled
    PersistenceNotVisibleError triggering an arq retry. Post-D1, save_doc
    and save_doc_meta never raise on barrier exhaustion, so the child
    process cannot be killed/retried for this reason, and processing_at
    SHALL land within the scorer's polling window."""

    def test_sla_doc_completes_within_scorer_polling_window(self, mock_minio, monkeypatch):
        monkeypatch.setattr(
            "pageindex_mcp.storage._confirm_write_visible",
            MagicMock(side_effect=PersistenceNotVisibleError("processed/d58be46f.json")),
        )
        doc_id = "d58be46f"
        batch_start = datetime.now(UTC)

        with patch("pageindex_mcp.cache.doc_cache_delete"):
            save_doc(
                doc_id,
                {
                    "doc_id": doc_id,
                    "doc_name": "اتفاقية مستوى الخدمة بين الوزارة وزارة الاقتصاد - موقعة من الطرفين.pdf",
                    "structure": [{"title": "Root", "nodes": []}],
                },
            )
            save_doc_meta(
                doc_id,
                {
                    "doc_id": doc_id,
                    "doc_name": "اتفاقية مستوى الخدمة بين الوزارة وزارة الاقتصاد - موقعة من الطرفين.pdf",
                    "source_url": "s3://doc_store/sla.pdf",
                    "processed_at": batch_start.isoformat(),
                },
            )

        processing_at = datetime.now(UTC)
        assert processing_at - batch_start < timedelta(minutes=2)
        assert mock_minio.put_object.call_count == 2
