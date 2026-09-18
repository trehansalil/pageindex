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

# RFC-038 D4 / RFC-046 D11 (task 3.12): hard cap on the effective timeout
# applied to a converter child, regardless of how many multipliers (chunked
# Docling timeout, 16.5x inspector multiplier) compound.
#
# Derivation (54000s = 15h): a 1000-page scanned PDF at MAX_DOCLING_PAGES=150
# yields chunk_count=7, effective_timeout = 3600 + 7*1500 = 14100s; the 16.5x
# inspector multiplier pushes that to ~232000s.  54000s caps it at ~15h, well
# above any legitimate conversion (world-stats-pocketbook at 292 pages takes
# <50min) but still bounded enough that a hung process doesn't block the queue
# indefinitely.  With MAX_JOBS_DEFAULT=1, one stuck document blocks the queue
# for at most this long.
#
# Safety rail, not a tuning knob. Env-configurable for deployments with
# exceptionally large documents.
MAX_EFFECTIVE_TIMEOUT: int = int(os.environ.get("MAX_EFFECTIVE_TIMEOUT", "54000"))

# RFC-032 D9: the timeout multiplier applied when the PDF inspector classifies a
# document as scanned/image-based with confidence >= INSPECTOR_CONFIDENCE_THRESHOLD.
# 3x was the unmeasured lower-end estimate; wall-clock calibration on 4 scanned
# corpus docs (2026-08-06) measured OCR-pass vs text-layer-pass ratios of
# 2.32x-11.00x (mean 6.16x, max 11.00x), exceeding D9's 5x recalibration
# threshold. Recalibrated per D9's formula: max(observed_ratio * 1.5, 3.0).
#
# NOTE (RFC-046 D11): this is applied BEFORE MAX_EFFECTIVE_TIMEOUT, and
# 16.5 * CHILD_TIMEOUT (59400s) already exceeds the 54000s cap at chunk_count=1
# -- so every inspector-detected scanned PDF receives exactly the cap and the
# chunk-proportional budget has no effect on that route. See
# test_inspector_multiplier_pins_every_scanned_pdf_to_the_cap.
INSPECTOR_OCR_MULTIPLIER: float = 16.5
