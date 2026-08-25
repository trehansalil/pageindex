"""Canonical timing constants for the worker subprocess pipeline.

Every timing-related value used across job.py, subprocess_mgr.py, and
indexer.py is defined here — nowhere else. This module has ZERO internal
imports to avoid circular dependency chains.
"""

import os

# RFC-028 D0: 1800 -> 3630 (max_dynamic_child_timeout 3300 + 300 buffer +
# CHILD_GRACE_SECONDS 30). arq's job_timeout is worker-level, not per-job, so
# raising it to cover the dynamic-timeout worst case (chunked_docling_timeout_s
# for large chunked PDFs) statically doubles worst-case slot occupancy for
# every job, not just large chunked PDFs. Accepted trade-off (see RFC-028
# Risks) -- world-stats-pocketbook-2023.pdf has ERRORed 3 consecutive runs.
JOB_TIMEOUT: int = 3630

# The inner timeout applied around the converter child must be strictly
# *shorter* than arq's outer ``job_timeout`` (JOB_TIMEOUT). Otherwise the two
# can race: arq cancels the task before our ``asyncio.timeout()`` fires and we
# skip the ``converter_timeout`` Redis status + metric increment.
# CHILD_GRACE_SECONDS is the margin reserved for "child timed out -> SIGTERM
# -> SIGKILL -> reap" plus clock skew between the asyncio loop and arq's
# wall-clock timer.
CHILD_GRACE_SECONDS: int = 30
CHILD_TIMEOUT: int = JOB_TIMEOUT - CHILD_GRACE_SECONDS

# A job legitimately runs up to JOB_TIMEOUT (arq's job_timeout). Past that plus
# a grace margin (clock skew + the gap before arq itself gives up) a hash still
# in status=processing means the worker died mid-job (e.g. OOMKill/SIGKILL ran
# no except/finally), so the reaper may safely mark it failed.
REAP_GRACE: int = 120

# RFC-038 D1: shared between indexer.py's forced-OCR gate and
# subprocess_mgr.py's 16.5x timeout multiplier so a document is never given
# the timeout budget for forced OCR without also triggering it (and vice versa).
INSPECTOR_CONFIDENCE_THRESHOLD: float = 0.90

# RFC-038 D4: hard cap on the effective timeout applied to a converter child,
# regardless of how many multipliers (chunked Docling timeout, 16.5x inspector
# multiplier) compound. Safety rail, not a tuning knob. Env-configurable for
# deployments with exceptionally large documents.
MAX_EFFECTIVE_TIMEOUT: int = int(os.environ.get("MAX_EFFECTIVE_TIMEOUT", "54000"))
