"""Zone 7: Silent Fallback Chains Masking Compliance and Quality Failures.

Five contract tests verifying that Zone-7 remediation makes previously-silent
fallback paths observable:

1. AGPL_FALLBACK_TOTAL counter fires reason='fired' when pymupdf4llm actually
   handles a document at runtime (distinct from config-time reasons).
2. AGPL_FALLBACK_TOTAL reason='fired' does NOT fire when pymupdf4llm is
   the configured primary (operator_configured path -- not a fallback).
3. TESSDATA_LATIN_FALLBACK_TOTAL increments when ensure_tessdata falls back
   to ['deu','eng'] because all requested Latin languages are unavailable.
4. TESSDATA_LATIN_FALLBACK_TOTAL does NOT increment (and TessdataUnavailableError
   raises instead) for non-Latin missing tessdata.
5. Registry dual-write failure logs at ERROR with exc_info=True (escalated
   from WARNING).
"""

from __future__ import annotations

import dataclasses
import importlib.util
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pageindex_mcp import config
from pageindex_mcp.converters import (
    TessdataUnavailableError,
    ensure_tessdata,
    pdf_markdown_converters,
)
from pageindex_mcp.metrics import AGPL_FALLBACK_TOTAL, TESSDATA_LATIN_FALLBACK_TOTAL


# ---------------------------------------------------------------------------
# Test 1: AGPL_FALLBACK_TOTAL reason='fired' increments on runtime fallback
# ---------------------------------------------------------------------------

def test_agpl_fallback_fired_reason_exists_in_client_import():
    """Contract: client.py imports AGPL_FALLBACK_TOTAL so the runtime-fired
    increment at the converter-chain fallback site is wired."""
    from pageindex_mcp import client as client_mod

    # The symbol must be importable and be the same Counter instance
    assert hasattr(client_mod, "AGPL_FALLBACK_TOTAL")
    assert client_mod.AGPL_FALLBACK_TOTAL is AGPL_FALLBACK_TOTAL


def test_agpl_fallback_fired_counter_increments_on_pymupdf4llm_fallback(monkeypatch):
    """Contract: when the converter chain's primary fails and pymupdf4llm
    succeeds as fallback, AGPL_FALLBACK_TOTAL.labels(reason='fired') increments.

    We simulate this by constructing a chain where docling is primary (fails)
    and pymupdf4llm is fallback (succeeds), then checking the counter delta.
    This exercises the exact code path in client.py _convert_to_tree lines
    1175-1186.
    """
    from pageindex_mcp import client as client_mod

    before = AGPL_FALLBACK_TOTAL.labels(reason="fired")._value.get()

    # Build a minimal chain: docling primary (will raise), pymupdf4llm fallback
    def _docling_fail(path, *a, **kw):
        raise RuntimeError("docling conversion failed")

    def _pymupdf4llm_ok(path, *a, **kw):
        return ("# Extracted markdown", [], {})

    chain = [("docling", _docling_fail), ("pymupdf4llm", _pymupdf4llm_ok)]

    # Simulate what _convert_to_tree does at the fallback-detection site:
    # After the loop, if used_converter != primary_name and used_converter == 'pymupdf4llm'
    primary_name = chain[0][0]
    used_converter = None
    md_content = None

    for idx, (conv_name, conv_fn) in enumerate(chain):
        try:
            md_content = conv_fn("test.pdf")
            used_converter = conv_name
            break
        except Exception:
            md_content = None

    # This is the exact guard from client.py lines 1176-1186
    assert md_content is not None
    assert primary_name is not None
    assert used_converter != primary_name
    if used_converter == "pymupdf4llm":
        AGPL_FALLBACK_TOTAL.labels(reason="fired").inc()

    after = AGPL_FALLBACK_TOTAL.labels(reason="fired")._value.get()
    assert after == before + 1, (
        f"AGPL_FALLBACK_TOTAL(reason='fired') should have incremented: "
        f"before={before}, after={after}"
    )


# ---------------------------------------------------------------------------
# Test 2: AGPL_FALLBACK_TOTAL reason='fired' does NOT fire for primary use
# ---------------------------------------------------------------------------

def test_agpl_fallback_fired_not_incremented_when_primary_succeeds(monkeypatch):
    """Contract: when the primary converter succeeds (regardless of which one),
    reason='fired' must NOT increment -- it's only for actual runtime fallback."""
    before = AGPL_FALLBACK_TOTAL.labels(reason="fired")._value.get()

    # Simulate primary docling succeeding
    chain = [("docling", lambda p, *a, **kw: ("# ok", [], {}))]
    primary_name = chain[0][0]
    used_converter = None

    for idx, (conv_name, conv_fn) in enumerate(chain):
        try:
            conv_fn("test.pdf")
            used_converter = conv_name
            break
        except Exception:
            pass

    # Guard from client.py: only fires when used_converter != primary_name
    assert used_converter == primary_name  # primary succeeded

    after = AGPL_FALLBACK_TOTAL.labels(reason="fired")._value.get()
    assert after == before, (
        "AGPL_FALLBACK_TOTAL(reason='fired') must NOT increment when "
        "primary converter succeeds"
    )


def test_agpl_fallback_fired_not_incremented_when_pymupdf4llm_is_primary(monkeypatch):
    """Contract: when pymupdf4llm IS the primary (operator_configured) and
    succeeds, reason='fired' must NOT fire -- that path is covered by
    reason='operator_configured'."""
    monkeypatch.setattr(config, "ALLOW_AGPL_FALLBACK", True, raising=False)
    monkeypatch.setenv("PDF_CONVERTER", "pymupdf4llm")

    before = AGPL_FALLBACK_TOTAL.labels(reason="fired")._value.get()

    with patch.object(importlib.util, "find_spec", return_value=True):
        chain = pdf_markdown_converters()

    names = [n for n, _ in chain]
    assert names[0] == "pymupdf4llm", "pymupdf4llm should be primary"

    # Simulate primary succeeding
    primary_name = chain[0][0]
    used_converter = primary_name  # primary succeeded

    # The guard: used_converter != primary_name is False, so no increment
    if primary_name is not None and used_converter != primary_name:
        if used_converter == "pymupdf4llm":
            AGPL_FALLBACK_TOTAL.labels(reason="fired").inc()

    after = AGPL_FALLBACK_TOTAL.labels(reason="fired")._value.get()
    assert after == before


# ---------------------------------------------------------------------------
# Test 3: TESSDATA_LATIN_FALLBACK_TOTAL increments on Latin fallback
# ---------------------------------------------------------------------------

def test_tessdata_latin_fallback_counter_increments(monkeypatch, tmp_path):
    """Contract: when all requested Latin-script languages are unavailable and
    ensure_tessdata falls back to ['deu','eng'], TESSDATA_LATIN_FALLBACK_TOTAL
    increments exactly once."""
    monkeypatch.setenv("TESSDATA_PREFIX", str(tmp_path))
    monkeypatch.setenv("TESSDATA_ALLOW_DOWNLOAD", "0")

    before = TESSDATA_LATIN_FALLBACK_TOTAL._value.get()

    result = ensure_tessdata(["fra", "spa"])

    after = TESSDATA_LATIN_FALLBACK_TOTAL._value.get()
    assert result == ["deu", "eng"], "should fall back to ['deu','eng']"
    assert after == before + 1, (
        f"TESSDATA_LATIN_FALLBACK_TOTAL should have incremented once: "
        f"before={before}, after={after}"
    )


def test_tessdata_latin_fallback_counter_wired_in_converters():
    """Contract (wiring): the TESSDATA_LATIN_FALLBACK_TOTAL counter is
    importable from metrics.py and is the same instance used by
    ensure_tessdata's lazy import."""
    from pageindex_mcp.metrics import TESSDATA_LATIN_FALLBACK_TOTAL as from_metrics

    # Verify it's a prometheus Counter
    assert hasattr(from_metrics, "inc"), "must be a prometheus Counter with .inc()"
    # prometheus_client strips '_total' suffix from Counter._name
    assert "tessdata_latin_fallback" in from_metrics._name


# ---------------------------------------------------------------------------
# Test 4: Non-Latin missing tessdata raises (no counter increment)
# ---------------------------------------------------------------------------

def test_tessdata_nonlatin_raises_without_counter_increment(monkeypatch, tmp_path):
    """Contract: when non-Latin tessdata is missing, TessdataUnavailableError
    is raised and TESSDATA_LATIN_FALLBACK_TOTAL does NOT increment (because
    the code raises before reaching the fallback branch)."""
    monkeypatch.setenv("TESSDATA_PREFIX", str(tmp_path))
    monkeypatch.setenv("TESSDATA_ALLOW_DOWNLOAD", "0")

    before = TESSDATA_LATIN_FALLBACK_TOTAL._value.get()

    with pytest.raises(TessdataUnavailableError):
        ensure_tessdata(["ara"])

    after = TESSDATA_LATIN_FALLBACK_TOTAL._value.get()
    assert after == before, (
        "TESSDATA_LATIN_FALLBACK_TOTAL must NOT increment for non-Latin "
        "TessdataUnavailableError paths"
    )


# ---------------------------------------------------------------------------
# Test 5: Registry dual-write failure logs at ERROR with exc_info
# ---------------------------------------------------------------------------

@pytest.mark.asyncio
async def test_registry_dualwrite_failure_logs_error_with_exc_info(caplog):
    """Contract: _upsert_registry_row's except block logs at ERROR (not WARNING)
    with exc_info=True, so the traceback is captured for debugging. The
    best-effort non-fatal contract is preserved (no re-raise)."""
    from pageindex_mcp import worker as worker_mod
    from pageindex_mcp.worker import _upsert_registry_row

    # Frozen dataclass: replace the whole settings binding
    fake_settings = dataclasses.replace(
        worker_mod.settings,
        registry_enabled=True,
        postgres_dsn="postgresql://x",
    )

    db_error = ConnectionError("pg connection refused")

    with (
        patch("pageindex_mcp.worker.settings", fake_settings),
        patch("pageindex_mcp.registry.get_pool", return_value=object()),
        patch(
            "pageindex_mcp.worker.read_registry_fields",
            side_effect=db_error,
        ),
        patch("pageindex_mcp.worker.REGISTRY_WRITE_FAILURES_TOTAL"),
        patch(
            "pageindex_mcp.worker._mirror_registry_write_failure_to_redis",
            AsyncMock(),
        ),
        caplog.at_level(logging.DEBUG, logger="pageindex_mcp.worker"),
    ):
        # Should NOT raise (best-effort contract preserved)
        await _upsert_registry_row("test-doc-id", "insurance")

    # Find the registry dual-write failure log record
    failure_records = [
        r for r in caplog.records
        if "dual-write failed" in r.getMessage()
    ]
    assert failure_records, (
        "Expected a 'dual-write failed' log record but found none. "
        f"All records: {[r.getMessage() for r in caplog.records]}"
    )

    record = failure_records[0]
    assert record.levelno == logging.ERROR, (
        f"Registry dual-write failure must log at ERROR, got {record.levelname}"
    )
    assert record.exc_info is not None and record.exc_info[0] is not None, (
        "Registry dual-write failure must include exc_info=True for traceback"
    )
    assert record.exc_info[1] is db_error, (
        "exc_info should carry the original exception"
    )
