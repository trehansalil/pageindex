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


@pytest.fixture(autouse=True)
def _reset_verdict_thresholds_cache():
    """Clear the VerdictThresholds cache before each test.

    ``classify_verdict`` uses ``_get_verdict_thresholds()`` which caches
    ``VerdictThresholds.from_env()`` at the module level.  Tests that set
    threshold env vars (e.g. ``PASS_MAX_LEAF_RATIO``, ``HYSTERESIS_BAND``)
    need the cache cleared so the new env values take effect.  Without this,
    test ordering determines which env snapshot is cached and later tests
    silently read stale thresholds.
    """
    from pageindex_mcp.helpers import reset_verdict_thresholds

    reset_verdict_thresholds()
    yield
    reset_verdict_thresholds()


_FILLER_WORDS = (
    "the",
    "quick",
    "brown",
    "fox",
    "jumps",
    "over",
    "a",
    "lazy",
    "dog",
    "and",
    "then",
    "runs",
    "to",
    "the",
    "river",
    "with",
    "of",
    "for",
)


def filler_text(n_chars: int, seed: int) -> str:
    """Return exactly *n_chars* of prose-shaped filler, phase-shifted by *seed*.

    RFC-033 D1 made ``_flatten_tree_text`` newline-separate node parts, so the
    flattened blob is now tokenized at node boundaries. Fixtures that gave every
    node the identical ``"x" * n`` snippet therefore hand ``_is_garbled_blob``
    one token repeated hundreds of times with no dictionary words in sight, and
    trip its garble heuristics — turning density/leaf-ratio fixtures into
    garbling fixtures. Real words, rotated per node, keep those tests measuring
    what they were written to measure. Character counts are exact, so the volume
    floors in ``classify_verdict`` are unaffected.
    """
    words: list[str] = []
    total = 0
    i = 0
    # " ".join(words) is total - 1 chars (no trailing space), so build until
    # the joined length (total - 1) reaches n_chars before truncating —
    # otherwise an exact landing returns n_chars - 1 and skews ratio fixtures.
    while total - 1 < n_chars:
        word = _FILLER_WORDS[(seed + i) % len(_FILLER_WORDS)]
        words.append(word)
        total += len(word) + 1
        i += 1
    out = " ".join(words)[:n_chars]
    # Never end on whitespace: callers assert on char counts that are measured
    # after a strip(), so a trailing space would silently shorten the fixture.
    return out[:-1] + "s" if out.endswith(" ") else out
