"""CustomPageIndexClient — remote conversion (Docling service)."""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import importlib
import logging
import math
import os
import time

from ..config import (
    CURRENT_PIPELINE_VERSION,
    RemoteVersionSkewError,
    ZDRComplianceError,
    pipeline_config,
    require_zdr_compliance,
    settings,
)
from ..metrics import (
    DOCLING_VERSION_SKEW,
    HR3_EGRESS_BLOCKED_TOTAL,
)
from ..obs.decisions import decision

logger = logging.getLogger(__name__)

# RFC-034 D1: cached remote Docling /version response, fetched once per process.
_remote_docling_version: dict | None = None
# Zone (converter-chain fallback): set to the remote ``pipeline_version`` when it
# was observed to be BEHIND the local ``CURRENT_PIPELINE_VERSION``.  Sticky for
# the process lifetime because ``_remote_docling_version`` itself is fetched only
# once — this lets the enforce-mode block re-evaluate on every conversion instead
# of only on the call that happened to perform the fetch.  ``None`` means "no
# skew observed" (including "/version was unreachable", which stays warn-only).
_remote_pipeline_version_behind: int | None = None
# Zone-7: BUILD_SHA is the convention services/docling-service's CI/Dockerfile
# already use; CLIENT_BUILD_SHA was a never-wired legacy name that left this
# permanently "unknown". Prefer BUILD_SHA, fall back to the legacy name.
_CLIENT_BUILD_SHA = os.environ.get("BUILD_SHA") or os.environ.get("CLIENT_BUILD_SHA", "unknown")
# RFC-052 R5 AC1/AC8: best-effort per-job /capacity snapshot + build-skew
# warning. Short timeout -- an older service (404), a timeout or a network
# error must never fail or meaningfully delay a conversion.
_CAPACITY_TIMEOUT_S = 2.0
# Dedup key: (backend, service_build_sha). Warn at most once per pair per
# process so a worker that is itself "unknown" in local dev does not spam a
# WARNING on every job.
_capacity_skew_warned: set[tuple[str, str]] = set()


async def _check_remote_docling_version(httpx_client) -> None:
    """RFC-034 D1: cache the remote Docling ``/version`` response and warn on skew.

    Fetched once per process. commit_sha is the primary skew signal (catches every
    converter-behaviour change); pipeline_version is a secondary, coarser signal.
    The commit_sha comparison is ``_build_sha_mismatch`` (RFC-052 R5 AC8's
    prefix rule), the same rule the per-job ``/capacity`` check uses, so the
    two skew signals in one process never disagree.

    When ``REMOTE_VERSION_ENFORCE`` is set, an observed ``pipeline_version``
    skew stops being advisory and raises :class:`RemoteVersionSkewError`, so a
    stale remote converter cannot silently produce trees stamped with the local
    pipeline version.  Default (``false``) is byte-identical warn-only behavior.
    """
    global _remote_docling_version, _remote_pipeline_version_behind
    if _remote_docling_version is None:
        try:
            ver_resp = await httpx_client.get(
                f"{settings.docling_service_url}/version", timeout=5.0
            )
            _remote_docling_version = ver_resp.json()
            remote_sha = _remote_docling_version.get("commit_sha", "unknown")
            remote_pv = _remote_docling_version.get("pipeline_version", 0)
            # F2: share the AC8 comparison rule with the /capacity check
            # (_build_sha_mismatch) instead of an exact-string compare, so a
            # 12-hex Mac SHA and a 40-hex CI SHA sharing that prefix agree
            # between the two call sites instead of one false-warning.
            if _build_sha_mismatch(_CLIENT_BUILD_SHA, remote_sha):
                logger.warning(
                    "Remote Docling SHA %s != client SHA %s", remote_sha, _CLIENT_BUILD_SHA
                )
                DOCLING_VERSION_SKEW.labels(signal="commit_sha").inc()
            if remote_pv < CURRENT_PIPELINE_VERSION:
                logger.error(
                    "Remote pipeline_version %d < local %d",
                    remote_pv,
                    CURRENT_PIPELINE_VERSION,
                )
                DOCLING_VERSION_SKEW.labels(signal="pipeline_version").inc()
                _remote_pipeline_version_behind = remote_pv
        except Exception as e:
            logger.warning("Could not fetch remote /version: %s; skew detection disabled", e)
            _remote_docling_version = {"commit_sha": "unavailable"}

    # Enforcement is re-evaluated on EVERY call — the /version response is
    # fetched once and cached, so gating inside the fetch block would block
    # only the first conversion of the process and let every later one through.
    # An unreachable /version never sets the flag and therefore stays warn-only.
    if _remote_pipeline_version_behind is not None and pipeline_config.remote_version_enforce:
        raise RemoteVersionSkewError(
            f"Remote Docling pipeline_version {_remote_pipeline_version_behind} < local "
            f"{CURRENT_PIPELINE_VERSION}; blocked by REMOTE_VERSION_ENFORCE. Redeploy the "
            f"Docling service or unset REMOTE_VERSION_ENFORCE to fall back to warn-only."
        )


def _build_sha_mismatch(local_sha: str, remote_sha: str) -> bool:
    """RFC-052 R5 AC8: is ``remote_sha`` (the backend's) skewed vs ``local_sha``
    (this worker's own ``BUILD_SHA``)?

    Compared on the shorter side's length: CI stamps the full 40-hex commit
    SHA, while the Mac's ``install.sh`` writes a 12-hex ``git rev-parse
    --short=12`` SHA. A 40-char and a 12-char SHA that share that 12-char
    prefix must NOT be treated as skewed.

    Either side being ``"unknown"`` or empty counts as a mismatch -- the
    contract gives no "unknown is fine" exception, so this errs toward
    warning. The caller dedupes per (backend, remote_sha) so a worker that is
    itself "unknown" in local dev warns once per backend, not once per job.

    F1: the shorter side must be at least 7 chars (git's default abbreviation
    length) to be compared at all. Without this floor, a degenerate SHA
    (e.g. a truncated ``BUILD_SHA="a"``) would share a trivial 1-char prefix
    with almost anything and silently suppress a real skew warning.
    """
    if not local_sha or not remote_sha or local_sha == "unknown" or remote_sha == "unknown":
        return True
    n = min(len(local_sha), len(remote_sha))
    if n < 7:
        return True
    return local_sha[:n] != remote_sha[:n]


async def _log_capacity_snapshot(
    client, headers: dict[str, str], *, base_url: str | None = None, warn_skew: bool = True
) -> None:
    """RFC-052 R5 AC1/AC8: GET the chosen backend's ``/capacity`` and log it.

    Best-effort only: a 404 (older service without the route), a timeout or
    any other network error is logged at DEBUG and swallowed -- this must
    never fail or meaningfully delay the conversion it rides along with, so
    it uses a short timeout and is never allowed to raise.

    The snapshot is logged as one structured decision record (HR3: no
    document content, only capacity numbers). Separately, a backend whose
    ``build_sha`` is skewed from this worker's own gets a WARNING, logged at
    most once per (backend, build_sha) per process (see
    ``_build_sha_mismatch`` and ``_capacity_skew_warned``).
    """
    try:
        resp = await client.get(
            f"{base_url or settings.docling_service_url}/capacity",
            headers=headers,
            timeout=_CAPACITY_TIMEOUT_S,
        )
        resp.raise_for_status()
        snapshot = resp.json()
    except Exception as e:
        logger.debug("Could not fetch /capacity snapshot: %s", e)
        return

    try:
        backend = snapshot.get("backend") or "unknown"
        decision(
            event="docling_capacity_snapshot",
            choice="logged",
            reason=backend,
            attrs={
                "backend": backend,
                "build_sha": snapshot.get("build_sha"),
                "effective_cpus": snapshot.get("effective_cpus"),
                "free_mem_bytes": snapshot.get("free_mem_bytes"),
                "safe_procs": snapshot.get("safe_procs"),
                "busy_slots": snapshot.get("busy_slots"),
                "max_slots": snapshot.get("max_slots"),
                "spp_ewma": snapshot.get("spp_ewma"),
                # "_count" suffix, not "spp_samples": that key trips the
                # decision-registry's content-attr substring guard ("sample").
                "spp_sample_count": snapshot.get("spp_samples"),
            },
        )

        remote_sha = snapshot.get("build_sha") or "unknown"
        key = (backend, remote_sha)
        # This IS the RFC-052 R5 AC8 build-skew check for P3 (single active
        # remote); P5 widens it to every eligible backend in the split.
        # A P5 split call (``warn_skew=False``) was already build-matched
        # against the document's expected build (R5 AC8 amendment A-P5-2);
        # comparing it with the worker's own SHA would only warn falsely.
        if (
            warn_skew
            and _build_sha_mismatch(_CLIENT_BUILD_SHA, remote_sha)
            and key not in _capacity_skew_warned
        ):
            _capacity_skew_warned.add(key)
            logger.warning(
                "Docling backend %s build_sha %s != worker build_sha %s",
                backend,
                remote_sha,
                _CLIENT_BUILD_SHA,
            )
    except Exception as e:  # pragma: no cover - logging must never break a job
        logger.debug("Could not process /capacity snapshot: %s", e)


def _converter_contract(converter_name: str | None) -> str | None:
    """RFC-034 D5: resolve the winning converter's module ``__version__``."""
    if not converter_name:
        return None
    try:
        module = importlib.import_module(converter_name)
        return getattr(module, "__version__", None)
    except Exception:
        return None


#: RFC-052 R1 AC6 / D3: correlation header -> obs context field. docling-service
#: binds these back into its own log context, so one ``job_id`` finds the
#: worker's lines and the remote conversion's lines alike.
_CORRELATION_HEADERS: tuple[tuple[str, str], ...] = (
    ("X-Job-Id", "job_id"),
    ("X-Doc-Sha8", "doc_sha8"),
    ("X-Run-Id", "run_id"),
)


def _correlation_headers(
    page_count: int | None = None, *, shard: str | None = None
) -> dict[str, str]:
    """Correlation headers from the current obs log context (RFC-052 R1 AC6).

    A field that is not bound is OMITTED -- never sent as ``"None"`` or ``""``,
    which the service would bind as a real value and every Grafana query on
    that id would then match.

    ``X-Shard`` is ``"<i>/<n>:<start>-<end>"`` with 0-based, inclusive page
    bounds (the same convention as ``docling_chunk``'s ``page_start`` /
    ``page_end``). Until the capacity split (RFC-052 P3) a document is always
    one shard, so it is ``"1/1:0-<page_count - 1>"``; with no known page count
    the header is omitted rather than guessed. A P5 split shard passes its own
    ``shard`` value (``"<i>/<n>:<start>-<end>"``), which wins.
    """
    from ..obs.context import current_context

    ctx = current_context()
    headers: dict[str, str] = {}
    for header, field in _CORRELATION_HEADERS:
        value = ctx.get(field)
        if value is not None and str(value) != "":
            headers[header] = str(value)
    if shard:
        headers["X-Shard"] = shard
    elif isinstance(page_count, int) and not isinstance(page_count, bool) and page_count > 0:
        headers["X-Shard"] = f"1/1:0-{page_count - 1}"
    return headers


#: Set by converters_cli at child start (coldstart Q5 item 7): wall-clock epoch
#: seconds past which the worker parent will have killed this child anyway.
ENV_CHILD_DEADLINE_EPOCH = "PAGEINDEX_CHILD_DEADLINE_EPOCH"


def _child_deadline_epoch() -> float | None:
    raw = os.environ.get(ENV_CHILD_DEADLINE_EPOCH)
    if not raw:
        return None
    try:
        return float(raw)
    except ValueError:
        return None


def child_deadline_monotonic() -> float | None:
    """The converter child's deadline on the ``time.monotonic()`` clock, or
    ``None`` outside a converter child (no deadline known)."""
    epoch = _child_deadline_epoch()
    if epoch is None:
        return None
    return time.monotonic() + (epoch - time.time())


# QA fix 2: production has DOCLING_SERVICE_TIMEOUT_S=3300 and a child capped
# at MAX_EFFECTIVE_TIMEOUT=3600 -- gate wait + an unclamped read can outlive
# the child, which then gets killed as converter_timeout instead of the call
# ever surfacing as DoclingUnavailable. Margin kept for the worker's own
# teardown/cleanup after the call would return.
_CHILD_DEADLINE_MARGIN_S = 30.0
# Below this much remaining time a call cannot plausibly finish usefully --
# raise DoclingUnavailable up front instead of making (and then abandoning) it.
_MIN_USEFUL_CALL_S = 60.0
# QA fix 3: X-Deadline must expire strictly BEFORE our own read timeout, or
# docling-service's poll-every-2s deadline check races the worker's read
# timeout and can return its 499 first (see _classify_transient_failure).
_X_DEADLINE_MARGIN_S = 5.0


def _effective_read_timeout_s() -> float | None:
    """QA fix 2: the read timeout, clamped to the converter child's remaining
    deadline (minus ``_CHILD_DEADLINE_MARGIN_S``) when one is known. ``None``
    means there is not enough time left for a useful call at all -- the
    caller must raise :class:`DoclingUnavailable` instead of dialing out."""
    read_s = float(settings.docling_service_timeout_s)
    child = _child_deadline_epoch()
    if child is not None:
        remaining = child - time.time() - _CHILD_DEADLINE_MARGIN_S
        read_s = min(read_s, remaining)
    if read_s < _MIN_USEFUL_CALL_S:
        return None
    return read_s


def _transport_timeout(read_s: float):
    """Coldstart Q5 item 3: a split timeout. The connect phase fails in
    ``docling_connect_timeout_s`` (a SYN blackhole used to hold for the whole
    read budget); only the read phase gets the (QA fix 2: clamped) read
    budget, so a slow call cannot outlive the converter child."""
    import httpx

    return httpx.Timeout(
        connect=settings.docling_connect_timeout_s,
        read=read_s,
        write=60.0,
        pool=10.0,
    )


def _deadline_header(headers: dict[str, str], read_s: float) -> None:
    """Coldstart Q5 item 7 / QA fix 3: ``X-Deadline`` (epoch seconds) -- the
    point past which nobody is waiting for this response. Set to strictly
    AFTER our own read timeout would already have fired (rounded up, plus
    ``_X_DEADLINE_MARGIN_S``), so a docling-service deadline poll (every 2s)
    never races ahead of -- and returns 499 before -- our own timeout."""
    deadline = math.ceil(time.time() + read_s) + _X_DEADLINE_MARGIN_S
    headers["X-Deadline"] = f"{deadline:.0f}"


class DoclingUnavailable(Exception):
    """No usable Docling backend after the readiness wait / retry budget.

    Transient by nature, and it must never turn silently into the legacy LLM
    page_index path: ``DOCLING_UNAVAILABLE_POLICY`` decides, observably. It
    crosses the converter-child boundary as the string ``"DoclingUnavailable"``
    in the child's error JSON; the worker matches on that name.
    """

    def __init__(self, message: str, *, waited_s: float = 0.0) -> None:
        super().__init__(message)
        self.waited_s = waited_s


@dataclasses.dataclass(frozen=True, slots=True)
class ReadyResult:
    ready: bool
    backend: str  # "mac" | "node" | "stale" | "local"
    waited_ms: int
    polls: int
    last_error: str | None = None


# Indirections so tests can drive the readiness wait with a fake clock.
_ready_sleep = asyncio.sleep
_ready_clock = time.monotonic

_HEALTH_CONNECT_S = 2.0
_HEALTH_READ_S = 3.0


async def _probe_health(client) -> str | None:
    """One ``GET /health``. ``None`` when the backend answers 200, else the
    failure as a bounded label (exception CLASS name or ``http_<status>``)."""
    try:
        resp = await client.get(f"{settings.docling_service_url}/health")
    except Exception as exc:
        return type(exc).__name__
    if resp.status_code == 200:
        return None
    return f"http_{resp.status_code}"


def _since_age_s(state: dict | None) -> float | None:
    """Seconds since the controller's ``since`` epoch; None when absent/bad."""
    try:
        return max(time.time() - float((state or {})["since"]), 0.0)
    except (KeyError, TypeError, ValueError):
        return None


def _backend_state_choice(state: dict | None) -> str:
    if not state:
        return "stale"
    if state.get("target") == "none" or state.get("phase") == "none":
        return "none"
    phase = state.get("phase")
    return phase if phase in ("ready", "starting", "down") else "stale"


def _backend_budget(state: dict | None, choice: str) -> tuple[str, float]:
    """``(backend, wait budget in seconds)`` per the coldstart Q4 table."""
    if choice == "stale":
        return "stale", settings.docling_ready_wait_s / 2
    target = state.get("target") if state else None
    if target == "node" and choice == "starting":
        started_ago = _since_age_s(state) or 0.0
        return "node", max(settings.docling_ready_wait_s - started_ago, 0.0)
    if target == "mac":
        return "mac", settings.docling_mac_wait_s
    # node ready/down: /health should answer at once; ride out a short blip.
    return "node", settings.docling_mac_wait_s


# QA fix 1: docling-node.sh publish_backend() reason strings (cmd_tick) for
# target=none that mean autostart itself cannot happen this tick or any
# future one (until an operator/day-rollover changes it) -- the daily
# AUTOSTART_MAX_PER_DAY cap, or DOCLING_AUTOSTART=0. Every OTHER target=none
# reason (e.g. "mac down, no demand", "no backend available") can flip on the
# very next tick, especially once this job's own in-progress wait counts as
# demand -- those are worth waiting out, not failing fast on.
_NONE_PERMANENT_REASON_MARKERS = ("autostart cap", "autostart disabled")


def _none_reason_is_permanent(reason: str | None) -> bool:
    reason = reason or ""
    return any(marker in reason for marker in _NONE_PERMANENT_REASON_MARKERS)


async def _wait_out_none(t0: float, deadline: float | None, state: dict | None) -> dict | None:
    """QA fix 1: poll ``docling:backend`` while ``target=none`` for a reason
    that is not permanent (see :func:`_none_reason_is_permanent`).

    Staying in-progress here (never a deferred requeue) is itself demand, so
    the very controller tick this waits on can be the one that starts
    docling-1. Budgeted by ``DOCLING_NONE_WAIT_S`` (default 90s -- at least
    two controller ticks), capped by ``deadline`` like every other wait here.

    Returns the first state where ``target`` is no longer ``none`` (it may
    still be ``down``/``starting``/``ready`` -- the caller re-derives the
    budget from it, same as the existing Mac-switch logic). Raises
    :class:`DoclingUnavailable` immediately for a permanent reason, or once
    the wait budget runs out.
    """
    from ..cache import get_docling_backend_state

    reason = (state or {}).get("reason")
    polls = 0
    while True:
        if _none_reason_is_permanent(reason):
            waited = _ready_clock() - t0
            decision(
                event="docling_readiness_wait",
                choice="no_backend_fail_fast",
                reason=reason or "no reason given",
                attrs={
                    "backend": "none",
                    "waited_ms": int(waited * 1000),
                    "polls": polls,
                    "budget_s": 0,
                },
            )
            raise DoclingUnavailable(f"no docling backend ({reason})")
        waited = _ready_clock() - t0
        budget_s = settings.docling_none_wait_s
        limit = budget_s if deadline is None else min(budget_s, deadline - t0)
        if waited + settings.docling_ready_poll_s > limit:
            decision(
                event="docling_readiness_wait",
                choice="no_backend_fail_fast",
                reason="autostart did not start within DOCLING_NONE_WAIT_S",
                attrs={
                    "backend": "none",
                    "waited_ms": int(waited * 1000),
                    "polls": polls,
                    "budget_s": budget_s,
                    "last_error": reason,
                },
            )
            raise DoclingUnavailable(
                f"no docling backend after {waited:.0f}s waiting for autostart "
                f"({reason or 'no reason given'})",
                waited_s=waited,
            )
        await _ready_sleep(settings.docling_ready_poll_s)
        polls += 1
        state = await get_docling_backend_state()
        choice = _backend_state_choice(state)
        reason = (state or {}).get("reason")
        if choice != "none":
            return state


async def wait_for_docling_ready(deadline: float | None = None) -> ReadyResult:
    """Coldstart Q4/Q5 item 2: the readiness gate before the first remote call.

    Reads the infra controller's ``docling:backend`` state (Redis db 1, TTL
    120 s) and polls ``GET /health`` (connect 2 s, read 3 s) every
    ``docling_ready_poll_s`` until it answers 200 or the backend's budget
    runs out -- whichever is sooner, and never past ``deadline`` (a
    ``time.monotonic()`` value):

    * ``target=none`` with a reason saying autostart is impossible (daily cap,
      disabled) -- fail fast, zero polls: nothing is coming.
    * ``target=none`` otherwise (e.g. "mac down, no demand") -- wait up to
      ``DOCLING_NONE_WAIT_S`` polling the controller key itself (this job's
      own wait is demand); once it shows node/starting, switch to the
      docling-1 budget below, measured from ``since``.
    * docling-1 ``starting`` -- wait ``docling_ready_wait_s`` measured from the
      controller's ``since`` (a cold start takes ~150 s; it is waited out
      inline, not requeued).
    * Mac -- a short ``docling_mac_wait_s``; if the controller has meanwhile
      started docling-1, switch to that budget, otherwise give up.
    * missing/stale key -- probe for half of ``docling_ready_wait_s``.

    Returns a ready :class:`ReadyResult`; raises :class:`DoclingUnavailable`.
    """
    import httpx

    from ..cache import get_docling_backend_state

    t0 = _ready_clock()
    if not settings.docling_service_url:
        decision(
            event="docling_readiness_wait",
            choice="skipped_local",
            reason="no remote docling service configured",
            attrs={"backend": "local", "waited_ms": 0, "polls": 0, "budget_s": 0},
        )
        return ReadyResult(ready=True, backend="local", waited_ms=0, polls=0)

    state = await get_docling_backend_state()
    choice = _backend_state_choice(state)
    since_age = _since_age_s(state)
    decision(
        event="docling_backend_state",
        choice=choice,
        reason="infra controller docling:backend at the readiness gate",
        attrs={
            "target": (state or {}).get("target"),
            "phase": (state or {}).get("phase"),
            "since_s": round(since_age, 1) if since_age is not None else None,
            "reason": (state or {}).get("reason"),
            "autostarts_today": (state or {}).get("autostarts_today"),
        },
    )
    if choice == "none":
        # QA fix 1: target=none is NOT always "autostart cannot happen". Most
        # of the time (e.g. "mac down, no demand") it just means the
        # controller has not seen demand yet -- and staying in-progress HERE
        # is demand, so the very next tick (~30-36s) can flip it to
        # node/starting. Only a reason that says autostart itself is
        # impossible (daily cap, disabled) is worth failing fast on with zero
        # polls; anything else gets DOCLING_NONE_WAIT_S before giving up.
        state = await _wait_out_none(t0, deadline, state)
        choice = _backend_state_choice(state)
        since_age = _since_age_s(state)
        decision(
            event="docling_backend_state",
            choice=choice,
            reason="infra controller docling:backend after the autostart wait",
            attrs={
                "target": (state or {}).get("target"),
                "phase": (state or {}).get("phase"),
                "since_s": round(since_age, 1) if since_age is not None else None,
                "reason": (state or {}).get("reason"),
                "autostarts_today": (state or {}).get("autostarts_today"),
            },
        )
        waited_so_far = _ready_clock() - t0
        backend, extra = _backend_budget(state, choice)
        budget_s = waited_so_far + extra
    else:
        backend, budget_s = _backend_budget(state, choice)
    polls = 0
    last_error: str | None = None
    timeout = httpx.Timeout(
        connect=_HEALTH_CONNECT_S, read=_HEALTH_READ_S, write=_HEALTH_READ_S, pool=_HEALTH_READ_S
    )
    headers: dict[str, str] = {}
    if settings.docling_service_bearer_token:
        headers["Authorization"] = f"Bearer {settings.docling_service_bearer_token}"
    async with httpx.AsyncClient(timeout=timeout, headers=headers) as client:
        mac_switched = False
        while True:
            polls += 1
            last_error = await _probe_health(client)
            waited = _ready_clock() - t0
            if last_error is None:
                decision(
                    event="docling_readiness_wait",
                    choice="ready",
                    reason="docling /health answered 200",
                    attrs={
                        "backend": backend,
                        "waited_ms": int(waited * 1000),
                        "polls": polls,
                        "budget_s": budget_s,
                    },
                )
                # Fold /version in: skew detection runs against the live
                # backend. Enforcement stays with the conversion call, where
                # the chain walker already handles RemoteVersionSkewError.
                with contextlib.suppress(RemoteVersionSkewError):
                    await _check_remote_docling_version(client)
                return ReadyResult(True, backend, int(waited * 1000), polls)
            limit = budget_s if deadline is None else min(budget_s, deadline - t0)
            if waited + settings.docling_ready_poll_s > limit:
                if backend == "mac" and not mac_switched:
                    # Q4: after the short Mac wait, follow a docling-1 start.
                    fresh = await get_docling_backend_state()
                    fresh_choice = _backend_state_choice(fresh)
                    if (fresh or {}).get("target") == "node" and fresh_choice == "starting":
                        mac_switched = True
                        backend, extra = _backend_budget(fresh, fresh_choice)
                        budget_s = waited + extra
                        continue
                    wait_choice = "mac_short_wait_expired"
                else:
                    wait_choice = "timeout"
                decision(
                    event="docling_readiness_wait",
                    choice=wait_choice,
                    reason="docling /health not ready within the wait budget",
                    attrs={
                        "backend": backend,
                        "waited_ms": int(waited * 1000),
                        "polls": polls,
                        "budget_s": budget_s,
                        "last_error": last_error,
                    },
                )
                raise DoclingUnavailable(
                    f"docling backend {backend} not ready after {waited:.0f}s "
                    f"({polls} polls, last {last_error})",
                    waited_s=waited,
                )
            await _ready_sleep(settings.docling_ready_poll_s)


@dataclasses.dataclass
class RemoteConvertResult:
    """Everything ``/convert/pdf`` returns (RFC-052 9.2 / R9 AC7).

    Pages in ``table_results`` / ``heading_pages`` / ``applied_chunks`` /
    picture ``page`` are 0-based WHOLE-DOCUMENT pages: a ``page_start`` slice
    is rebased here, so callers never see slice-relative pages. An older
    service without the new fields yields empty lists.
    """

    markdown: str
    pictures: list
    table_results: list[dict] = dataclasses.field(default_factory=list)
    heading_pages: list[tuple[str, int]] = dataclasses.field(default_factory=list)
    applied_chunks: list[dict] = dataclasses.field(default_factory=list)
    applied: dict | None = None
    page_start: int = 0


def _as_int(value: object) -> int | None:
    if isinstance(value, bool) or not isinstance(value, (int, float)):
        return None
    return int(value)


def _rebase_convert_extras(data: dict, page_start: int) -> tuple[list, list, list]:
    """``(table_results, heading_pages, applied_chunks)`` from a response
    body, pages shifted by *page_start*. Malformed entries are dropped: the
    extras are advisory and must never fail a conversion."""
    tables: list[dict] = []
    for t in data.get("table_results") or []:
        page = _as_int(t.get("page")) if isinstance(t, dict) else None
        if page is None:
            continue
        tables.append({**t, "page": page + page_start})
    headings: list[tuple[str, int]] = []
    for h in data.get("heading_pages") or []:
        if isinstance(h, (list, tuple)) and len(h) == 2 and _as_int(h[1]) is not None:
            headings.append((str(h[0]), int(h[1]) + page_start))
    chunks: list[dict] = []
    applied = data.get("applied")
    for c in (applied.get("chunks") if isinstance(applied, dict) else None) or []:
        if not isinstance(c, dict):
            continue
        entry = dict(c)  # verbatim (P4-9)
        for key in ("page_start", "page_end"):
            v = _as_int(entry.get(key))
            if v is not None:
                entry[key] = v + page_start
        if isinstance(entry.get("tableformer_pages"), list):
            pages = (_as_int(x) for x in entry["tableformer_pages"])
            entry["tableformer_pages"] = [p + page_start for p in pages if p is not None]
        chunks.append(entry)
    return tables, headings, chunks


def merge_convert_results(results: list[RemoteConvertResult]) -> RemoteConvertResult:
    """Merge already-rebased P3 shard results in page order: markdown is
    concatenated by ``page_start``; every page-keyed list is concatenated in
    the same order (their pages are whole-document already)."""
    ordered = sorted(results, key=lambda r: r.page_start)
    return RemoteConvertResult(
        markdown="\n\n".join(r.markdown for r in ordered if r.markdown),
        pictures=[p for r in ordered for p in r.pictures],
        table_results=[t for r in ordered for t in r.table_results],
        heading_pages=[h for r in ordered for h in r.heading_pages],
        applied_chunks=[c for r in ordered for c in r.applied_chunks],
        applied=ordered[0].applied if ordered else None,
        page_start=ordered[0].page_start if ordered else 0,
    )


async def _remote_pdf_convert(
    staging_key: str,
    *,
    force_full_page_ocr: bool = False,
    ocr_lang_override: list[str] | None = None,
    expected_script: str | None = None,
    pages_with_tables: list[int] | None = None,
    page_count: int | None = None,
    page_classes: list[list] | None = None,
    page_start: int | None = None,
    page_end: int | None = None,
    prior_pass: list[dict] | None = None,
    recovery_trigger: str | None = None,
    base_url: str | None = None,
    read_timeout_s: float | None = None,
    shard: str | None = None,
    hr3_checked: bool = False,
) -> RemoteConvertResult:
    """Call the external Docling service to convert a PDF.

    ``png_bytes`` in each picture result is decoded from base64 back to bytes.

    ``expected_script`` is the caller's script expectation for the document
    (e.g. ``"latin"``, ``"arabic"``), matching the local converter's parameter
    of the same name.  It is forwarded as the ``expected_script`` payload key
    so a server-side garble check can use it instead of re-inferring the script
    from the extracted text.  A remote build that does not know the key ignores
    it, so sending it is safe against both old and new Docling services.

    ``page_count`` (when known) only shapes the ``X-Shard`` correlation header;
    it never changes the payload. See ``_correlation_headers``.

    ``page_classes`` is the run-length wire form from the preclassify
    handshake (``[[start, end, "T-t"], ...]``, RFC-052 R2 AC6), forwarded
    verbatim. Like ``expected_script`` it is safe against an older service:
    its request model ignores unknown keys.

    ``page_start``/``page_end`` (P3 R5 AC3, both or neither) slice the
    conversion; the response's page-keyed fields are rebased by
    ``page_start`` before they are returned. ``prior_pass`` (whole-document
    pages) and ``recovery_trigger`` ride on an HR5 recovery request (R9 AC7)
    and are sent only when given -- ``None`` is omitted, never sent as ``[]``.

    RFC-052 P5: an unsliced call with ``DOCLING_SPLIT_ENABLED=1`` goes to the
    split coordinator (``client/split.py``) first; it returns ``None`` when no
    named backend is eligible, and the call then proceeds as before. The
    coordinator's own calls pass ``base_url`` (a named backend instead of
    ``docling-active``), ``read_timeout_s`` (what is left of the shared
    deadline), ``shard`` (the ``X-Shard`` value) and ``hr3_checked`` (the
    backend already passed the per-backend HR3 eligibility, R5 AC7).
    """
    import base64

    import httpx

    from ..storage import presigned_get_url

    # ``is True``: stand-in settings objects (SimpleNamespace / MagicMock) must
    # never switch the split on by accident.
    if (
        base_url is None
        and page_start is None
        and getattr(settings, "docling_split_enabled", False) is True
    ):
        from .split import split_convert

        split_res = await split_convert(
            staging_key,
            page_count=page_count,
            page_classes=page_classes,
            convert_kwargs={
                "force_full_page_ocr": force_full_page_ocr,
                "ocr_lang_override": ocr_lang_override,
                "expected_script": expected_script,
                "pages_with_tables": pages_with_tables,
                "prior_pass": prior_pass,
                "recovery_trigger": recovery_trigger,
            },
        )
        if split_res is not None:
            return split_res

    service_url = base_url or settings.docling_service_url
    if settings.pii_corpus and not hr3_checked:
        try:
            require_zdr_compliance(service_url, "Docling remote PDF conversion")
        except ZDRComplianceError:
            HR3_EGRESS_BLOCKED_TOTAL.labels(path="docling_pdf").inc()
            raise

    url = presigned_get_url(staging_key)
    payload: dict = {
        "presigned_url": url,
        "force_full_page_ocr": force_full_page_ocr,
        "ocr_lang_override": ocr_lang_override,
        "expected_script": expected_script,
        "pages_with_tables": pages_with_tables,
        "page_classes": page_classes,
    }
    if page_start is not None and page_end is not None:
        payload["page_start"] = page_start
        payload["page_end"] = page_end
    if prior_pass is not None:
        payload["prior_pass"] = prior_pass
    if recovery_trigger is not None:
        payload["recovery_trigger"] = recovery_trigger
    # QA fix 2: a read timeout longer than the child's remaining life just
    # gets killed as converter_timeout instead of ever raising here -- clamp
    # it, and refuse to dial out at all when too little time is left.
    read_s = _effective_read_timeout_s()
    if read_s is not None and read_timeout_s is not None:
        read_s = min(read_s, read_timeout_s)
        if read_s < _MIN_USEFUL_CALL_S:
            read_s = None
    if read_s is None:
        raise DoclingUnavailable(
            f"not enough time left for a docling call "
            f"(< {_MIN_USEFUL_CALL_S:.0f}s remaining before the child deadline)"
        )
    headers: dict[str, str] = _correlation_headers(page_count, shard=shard)
    _deadline_header(headers, read_s)
    if settings.docling_service_bearer_token:
        headers["Authorization"] = f"Bearer {settings.docling_service_bearer_token}"
    async with httpx.AsyncClient(timeout=_transport_timeout(read_s)) as client:
        if base_url is None:
            # The one-per-process /version check is docling-active's; a split
            # shard's build was already matched from its /capacity (R5 AC8).
            await _check_remote_docling_version(client)
        resp = await client.post(
            f"{service_url}/convert/pdf",
            json=payload,
            headers=headers,
        )
        resp.raise_for_status()
        data = resp.json()
        # RFC-052 R5 AC1/AC8: best-effort capacity snapshot + build-skew
        # warning, same bearer token as /convert. Never allowed to fail or
        # delay the conversion -- the result is already in hand.
        capacity_headers: dict[str, str] = {}
        if settings.docling_service_bearer_token:
            capacity_headers["Authorization"] = f"Bearer {settings.docling_service_bearer_token}"
        await _log_capacity_snapshot(
            client, capacity_headers, base_url=service_url, warn_skew=base_url is None
        )
    shift = page_start if page_start is not None and page_end is not None else 0
    pic_results: list[dict] = []
    for pr in data.get("picture_results", []):
        raw_b64 = pr.get("png_bytes", "")
        if raw_b64:
            pr["png_bytes"] = base64.b64decode(raw_b64)
        else:
            pr["png_bytes"] = b""
        if shift and _as_int(pr.get("page")) is not None:
            pr["page"] = int(pr["page"]) + shift
        pic_results.append(pr)
    tables, headings, chunks = _rebase_convert_extras(data, shift)
    return RemoteConvertResult(
        markdown=data["markdown"],
        pictures=pic_results,
        table_results=tables,
        heading_pages=headings,
        applied_chunks=chunks,
        applied=data.get("applied") if isinstance(data.get("applied"), dict) else None,
        page_start=shift,
    )


async def _remote_pdf_to_markdown(
    staging_key: str,
    *,
    force_full_page_ocr: bool = False,
    ocr_lang_override: list[str] | None = None,
    expected_script: str | None = None,
    pages_with_tables: list[int] | None = None,
    page_count: int | None = None,
    page_classes: list[list] | None = None,
) -> tuple[str, list]:
    """``(markdown, pic_results)`` view of :func:`_remote_pdf_convert`, the
    same shape as the local ``pdf_to_markdown_docling()`` -- callers are
    oblivious to the transport."""
    res = await _remote_pdf_convert(
        staging_key,
        force_full_page_ocr=force_full_page_ocr,
        ocr_lang_override=ocr_lang_override,
        expected_script=expected_script,
        pages_with_tables=pages_with_tables,
        page_count=page_count,
        page_classes=page_classes,
    )
    return res.markdown, res.pictures


async def _remote_image_to_markdown(
    staging_key: str,
    *,
    ocr_lang_override: list[str] | None = None,
) -> str:
    """Call the external Docling service to convert an image to markdown."""
    import httpx

    from ..storage import presigned_get_url

    if settings.pii_corpus:
        try:
            require_zdr_compliance(settings.docling_service_url, "Docling remote image conversion")
        except ZDRComplianceError:
            HR3_EGRESS_BLOCKED_TOTAL.labels(path="docling_image").inc()
            raise

    url = presigned_get_url(staging_key)
    payload = {
        "presigned_url": url,
        "ocr_lang_override": ocr_lang_override,
    }
    # QA fix 2: same clamp as _remote_pdf_to_markdown.
    read_s = _effective_read_timeout_s()
    if read_s is None:
        raise DoclingUnavailable(
            f"not enough time left for a docling call "
            f"(< {_MIN_USEFUL_CALL_S:.0f}s remaining before the child deadline)"
        )
    # An image is a single page: always one shard, page 0.
    headers: dict[str, str] = _correlation_headers(page_count=1)
    _deadline_header(headers, read_s)
    if settings.docling_service_bearer_token:
        headers["Authorization"] = f"Bearer {settings.docling_service_bearer_token}"
    async with httpx.AsyncClient(timeout=_transport_timeout(read_s)) as client:
        resp = await client.post(
            f"{settings.docling_service_url}/convert/image",
            json=payload,
            headers=headers,
        )
        resp.raise_for_status()
        data = resp.json()
    return data["markdown"]
