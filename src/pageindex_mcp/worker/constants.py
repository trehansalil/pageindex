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

# RFC-038 D4 / RFC-046 D11: hard cap on the effective timeout applied to a
# converter child, regardless of how many multipliers (chunked Docling timeout,
# INSPECTOR_OCR_MULTIPLIER) compound. Safety rail, not a tuning knob.
#
# 2026-09-18: 54000s (15h) -> 3600s (60 min), by explicit operator decision.
#
# The 15h figure was derived from the worst *conversion* case (a 1000-page
# scanned PDF: chunk_count=7 -> 14100s, x16.5 -> ~232000s, capped at 54000s).
# It bounded nothing that mattered operationally: with MAX_JOBS_DEFAULT=1 one
# stuck document held the only worker slot for fifteen hours, and because arq
# derives its in-progress key TTL from the largest function timeout
# (worker.py: max_timeout + 10), a hard worker death (OOMKill -- this host has
# had them) blocked that job's re-queue for the same fifteen hours. 60 minutes
# bounds both.
#
# Consequences, stated rather than discovered later:
#   * The cap now EQUALS CHILD_TIMEOUT. Any chunked conversion (chunk_count>=2)
#     requests more than 3600s and is capped back down to it, so the
#     chunk-proportional budget RFC-028 D7 added has no effect while this value
#     stands. effective_child_timeout() still computes it, and ChildTimeout.capped
#     reports the shortfall -- subprocess_mgr logs a warning naming both numbers,
#     so a document killed this way says so instead of looking like a hang.
#   * RFC-028 raised JOB_TIMEOUT to 3630 because world-stats-pocketbook-2023.pdf
#     (292 pages, <50 min observed) ERRORed three consecutive runs at 1800s. It
#     fits inside 60 min, but with under 10 minutes to spare.
#   * Documents that genuinely need longer are a deployment-level override, which
#     is what the env var is for -- not a code change.
MAX_EFFECTIVE_TIMEOUT: int = int(os.environ.get("MAX_EFFECTIVE_TIMEOUT", "3600"))

# RFC-032 D9: the timeout multiplier applied when the PDF inspector classifies a
# document as scanned/image-based with confidence >= INSPECTOR_CONFIDENCE_THRESHOLD.
# 3x was the unmeasured lower-end estimate; wall-clock calibration on 4 scanned
# corpus docs (2026-08-06) measured OCR-pass vs text-layer-pass ratios of
# 2.32x-11.00x (mean 6.16x, max 11.00x), exceeding D9's 5x recalibration
# threshold. Recalibrated per D9's formula: max(observed_ratio * 1.5, 3.0).
#
# NOTE (RFC-046 D11): this is applied BEFORE MAX_EFFECTIVE_TIMEOUT, and
# 16.5 * CHILD_TIMEOUT (59400s) exceeds MAX_EFFECTIVE_TIMEOUT at chunk_count=1
# at any cap value this project has used -- so every inspector-detected scanned
# PDF receives exactly the cap, and the chunk-proportional budget has no effect
# on that route. See test_inspector_multiplier_pins_every_scanned_pdf_to_the_cap.
INSPECTOR_OCR_MULTIPLIER: float = 16.5
