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

``pageindex_mcp.config`` calls ``load_dotenv()`` at import time, so a developer's
local ``.env`` (e.g. ``PAGEINDEX_WORKER_MAX_JOBS=10`` for a remote-Docling profile)
leaks into the test process's real environment. ``pageindex_mcp.worker`` reads
``PAGEINDEX_WORKER_MAX_JOBS`` via ``os.getenv`` at *module import time* into the
module-level ``MAX_JOBS`` constant, so the leak has to be pre-empted here, before
pytest imports any test module that imports ``pageindex_mcp.worker`` (importing
``pageindex_mcp.worker`` is itself what triggers ``pageindex_mcp.config``'s
``load_dotenv()`` call, so a fixture would run too late either way).

Simply deleting the key is not enough: ``load_dotenv()`` only *skips* a key that
is already present in ``os.environ`` (``override=False`` is its default), so a
missing key gets repopulated straight from ``.env`` the moment
``pageindex_mcp.config`` is imported. Setting it to the empty string instead
makes it "already present" (parses to the memory-safe default via
``resolve_max_jobs``) so ``load_dotenv()`` leaves it alone.
"""

from __future__ import annotations

import os
from unittest.mock import patch

import pytest

# Pre-empt .env-sourced worker-concurrency overrides before any test module
# (and therefore pageindex_mcp.worker, transitively pageindex_mcp.config's
# load_dotenv()) is imported, so WORKER-02-C1's memory-safe default
# (max_jobs == 1) is what the suite actually exercises, regardless of what a
# developer's local .env sets for a remote-Docling profile.
os.environ["PAGEINDEX_WORKER_MAX_JOBS"] = ""


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
