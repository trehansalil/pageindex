"""Shared pytest fixtures.

The memory-admission gate (``pageindex_mcp.worker.wait_for_memory``) reads the
node's real ``/proc/meminfo`` and sleeps up to ``MEM_ADMISSION_MAX_WAIT_S`` (120s)
when headroom is below the floor. On a memory-tight CI/dev node that blocks every
job-level worker test for the full wait cap. We neutralize it suite-wide here so
worker tests exercise the pipeline, not the gate's polling loop.

Tests that specifically assert gate behavior (e.g.
``test_process_document_job_awaits_memory_gate_before_subprocess``) wrap their own
``patch("pageindex_mcp.worker.wait_for_memory", ...)``; that inner patch takes
precedence over this fixture. The gate's own logic is covered directly in
``tests/test_memory_admission.py``, which calls the functions under test and is
unaffected by this patch on the worker module's reference.
"""

from __future__ import annotations

from unittest.mock import patch

import pytest


@pytest.fixture(autouse=True)
def _instant_memory_gate():
    """Make the worker's admission gate return immediately during tests."""

    async def _proceed(_redis):
        return True

    try:
        with patch("pageindex_mcp.worker.wait_for_memory", _proceed):
            yield
    except (ImportError, AttributeError):
        # Worker module not importable in this context — nothing to patch.
        yield
