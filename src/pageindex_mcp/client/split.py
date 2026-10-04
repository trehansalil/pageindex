"""RFC-052 P5: split one PDF's conversion across the Mac and docling-1.

The coordinator runs in the converter child, next to ``_remote_pdf_convert``
(design D8). With ``DOCLING_SPLIT_ENABLED=0`` nothing here runs and the whole
document goes to ``docling-active`` as before (R5 AC10, property P5).

Flow (design "Allocation" / "Failure, deadline and merge"):

1. Probe ``/capacity`` on every configured backend (the named Services, never
   ``docling-active``). A backend is eligible only when it answered, its HR3
   policy allows the document (R5 AC7), its build matches the expected build
   (R5 AC8), ``safe_procs >= 1`` and it has a free slot.
2. No eligible backend: fall back to today's path. One: the whole document,
   unsliced, to that backend. Two or more: split.
3. Split: R3 page-class chunks; each backend gets a contiguous initial block
   in proportion to ``rate = safe_procs / spp_ewma``, covering
   ``DOCLING_SPLIT_INITIAL_FRAC`` of the pages, the fastest at the front. The
   rest is cut into tail shards that idle backends pull (tail stealing).
4. One shared deadline for all shards. A failed shard is retried once, on
   another backend when one is live; a second failure fails the conversion
   with the shard's own exception, so the worker's retry policy is unchanged.
5. Merge by ``page_start`` and count heading jumps at shard joins (R6 AC2).
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import re
import time

from ..config import settings
from ..obs.decisions import decision

logger = logging.getLogger(__name__)

# Backends that refused a connection are skipped for this long (design:
# "a backend that refuses connections is marked absent (cached 60 s)").
_ABSENT_TTL_S = 60.0
_CAPACITY_TIMEOUT_S = 2.0
# How long a backend waits before re-reading /capacity after it reported
# itself busy or out of memory for a tail shard; also the scheduler's tick.
_BUSY_POLL_S = 5.0
# HR3 (R5 AC7, NG6): the Mac is never eligible for a PII document, whatever
# DOCLING_SPLIT_PII_BACKENDS says.
_NEVER_PII = frozenset({"mac"})
# No samples yet and no prior from the service: the design's cpx62 prior.
_DEFAULT_SPP = 40.0

_absent_until: dict[str, float] = {}
_HEADING_RE = re.compile(r"^(#{1,6})\s", re.MULTILINE)


@dataclasses.dataclass(frozen=True)
class SplitBackend:
    name: str
    url: str


@dataclasses.dataclass(frozen=True)
class Capacity:
    build_sha: str
    safe_procs: int
    busy_slots: int
    max_slots: int
    spp_ewma: float
    chunk_pages: int
    # The service's own identity (``DOCLING_BACKEND_NAME``, else hostname):
    # the HR3 check uses it so a Mac configured under another alias is
    # still recognised as the Mac.
    backend: str = ""

    @classmethod
    def from_snapshot(cls, snap: dict) -> Capacity:
        def _num(key, default, cast):
            try:
                return cast(snap.get(key, default))
            except (TypeError, ValueError):
                return default

        spp = _num("spp_ewma", 0.0, float)
        return cls(
            build_sha=str(snap.get("build_sha") or "unknown"),
            safe_procs=_num("safe_procs", 0, int),
            busy_slots=_num("busy_slots", 0, int),
            max_slots=_num("max_slots", 1, int),
            spp_ewma=spp if spp > 0 else _DEFAULT_SPP,
            chunk_pages=max(1, _num("chunk_pages", 10, int)),
            backend=str(snap.get("backend") or ""),
        )

    @property
    def rate(self) -> float:
        """Pages per second this backend converts (``safe_procs / spp_ewma``)."""
        return self.safe_procs / self.spp_ewma

    @property
    def has_free_slot(self) -> bool:
        return self.busy_slots < self.max_slots

    @property
    def accepts_work(self) -> bool:
        return self.safe_procs >= 1 and self.has_free_slot


@dataclasses.dataclass(frozen=True)
class Shard:
    index: int
    start: int
    end: int

    @property
    def pages(self) -> int:
        return self.end - self.start + 1


def configured_backends(spec: str | None = None) -> list[SplitBackend]:
    """Parse ``DOCLING_SPLIT_BACKENDS`` (``name=url,...``); bad entries are skipped."""
    raw = settings.docling_split_backends if spec is None else spec
    out: list[SplitBackend] = []
    for part in (raw or "").split(","):
        name, sep, url = part.strip().partition("=")
        if sep and name.strip() and url.strip():
            out.append(SplitBackend(name.strip(), url.strip().rstrip("/")))
    return out


def hr3_eligible(
    name: str,
    *,
    pii_corpus: bool,
    url: str = "",
    reported: str = "",
    pii_backends: str | None = None,
) -> bool:
    """R5 AC7: may backend ``name`` see this corpus's documents?

    Without PII every backend may. With PII a backend must pass all three:

    - neither its configured ``name`` nor the identity the service itself
      reports in ``/capacity`` (``reported``) is the Mac, case-insensitively,
      so a Mac configured under another alias is still refused;
    - ``name`` is listed in ``DOCLING_SPLIT_PII_BACKENDS``;
    - ``url`` is a cluster-internal Service address (a bare Service name or a
      ``*.svc`` / ``*.svc.cluster.local`` host), never a public or Tailscale
      address. This is the self-hosted counterpart of
      ``require_zdr_compliance``, whose allow-list covers only LLM APIs.
    """
    from urllib.parse import urlparse

    if not pii_corpus:
        return True
    identities = {name.strip().lower(), reported.strip().lower()} - {""}
    if identities & _NEVER_PII:
        return False
    raw = settings.docling_split_pii_backends if pii_backends is None else pii_backends
    allowed = {p.strip().lower() for p in (raw or "").split(",") if p.strip()}
    if name.strip().lower() not in allowed:
        return False
    host = (urlparse(url).hostname or "").lower()
    return bool(host) and (
        "." not in host or host.endswith(".svc") or host.endswith(".svc.cluster.local")
    )


def build_matches(expected: str, actual: str) -> bool:
    """Same build, compared on the shorter SHA (CI stamps 40 hex, the Mac 12)."""
    if not expected or not actual or "unknown" in (expected, actual):
        return False
    n = min(len(expected), len(actual))
    return n >= 7 and expected[:n] == actual[:n]


def doc_chunks(
    page_count: int, page_classes: list | None, chunk_pages: int
) -> list[tuple[int, int]]:
    """R3 chunk boundaries for the whole document, ``(start, end)`` inclusive.

    Shards are cut only at these boundaries, so a split adds no join the
    service would not make anyway (risk table: RFC-027 D7). Without usable
    page classes the chunks are uniform ``chunk_pages`` runs.
    """
    from ..converters.docling_conv import _page_classes_active
    from ..converters.docling_resources import MAX_CHUNK_PAGES, MIN_CHUNK_PAGES
    from ..converters.page_class_chunker import page_class_chunks
    from ..converters.preclassify import page_classes_from_ranges

    classes = None
    try:
        classes = page_classes_from_ranges(page_classes)
    except ValueError as e:
        logger.warning("split: ignoring page classes: %s", e)
    if classes is not None and _page_classes_active(classes, page_count):
        return [
            (c.start, c.end)
            for c in page_class_chunks(
                classes, max_pages=MAX_CHUNK_PAGES, min_pages=MIN_CHUNK_PAGES
            )
        ]
    step = max(1, chunk_pages)
    return [(s, min(s + step, page_count) - 1) for s in range(0, page_count, step)]


def plan_split(
    chunks: list[tuple[int, int]],
    caps: dict[str, Capacity],
    *,
    initial_frac: float,
) -> tuple[dict[str, Shard], list[Shard]]:
    """Initial contiguous block per backend plus the tail shards (pure).

    Backends are ordered by rate, fastest first, and take consecutive blocks
    from the front of the document, each sized ``initial_frac * N * rate /
    sum(rate)`` and snapped to chunk boundaries. Every backend gets at least
    one chunk while chunks remain. A tail shard holds at most
    ``min(safe_procs)`` chunks, so any backend fills its processes in one wave.
    """
    if not chunks or not caps:
        return {}, []
    order = sorted(caps, key=lambda n: (-caps[n].rate, n))
    total_rate = sum(caps[n].rate for n in order) or 1.0
    n_pages = chunks[-1][1] - chunks[0][0] + 1
    initial: dict[str, Shard] = {}
    idx = 0
    for name in order:
        if idx >= len(chunks):
            break
        target = initial_frac * n_pages * caps[name].rate / total_rate
        got = 0
        first = idx
        while idx < len(chunks):
            size = chunks[idx][1] - chunks[idx][0] + 1
            # Take a chunk while that keeps us nearer the target; always take
            # one so every eligible backend starts at once.
            if idx > first and got + size / 2 > target:
                break
            got += size
            idx += 1
        initial[name] = Shard(len(initial), chunks[first][0], chunks[idx - 1][1])
    per_tail = max(1, min(caps[n].safe_procs for n in order))
    tail: list[Shard] = []
    while idx < len(chunks):
        last = min(idx + per_tail, len(chunks)) - 1
        tail.append(Shard(len(initial) + len(tail), chunks[idx][0], chunks[last][1]))
        idx = last + 1
    return initial, tail


def join_heading_shifts(markdowns: list[str]) -> int:
    """R6 AC2: joins where the next shard opens more than one heading level
    deeper than the previous shard's last heading (a re-levelling artifact).
    ``markdowns`` must already be in page order."""
    shifts = 0
    prev_last: int | None = None
    for md in markdowns:
        levels = [len(m.group(1)) for m in _HEADING_RE.finditer(md or "")]
        if not levels:
            continue
        if prev_last is not None and levels[0] > prev_last + 1:
            shifts += 1
        prev_last = levels[-1]
    return shifts


def _is_absent(url: str) -> bool:
    until = _absent_until.get(url)
    return until is not None and until > time.monotonic()


def _mark_absent(url: str) -> None:
    _absent_until[url] = time.monotonic() + _ABSENT_TTL_S


async def _fetch_capacity(client, backend: SplitBackend) -> Capacity | None:
    """``/capacity`` of one backend; ``None`` when it does not answer.

    A refused or timed-out connection marks the backend absent for 60 s.
    """
    import httpx

    headers = {}
    if settings.docling_service_bearer_token:
        headers["Authorization"] = f"Bearer {settings.docling_service_bearer_token}"
    try:
        resp = await client.get(
            f"{backend.url}/capacity", headers=headers, timeout=_CAPACITY_TIMEOUT_S
        )
        resp.raise_for_status()
        return Capacity.from_snapshot(resp.json())
    except (httpx.ConnectError, httpx.ConnectTimeout):
        _mark_absent(backend.url)
        return None
    except Exception as e:
        logger.debug("split: /capacity of %s failed: %s", backend.name, e)
        return None


async def _expected_build_sha(caps: dict[str, Capacity]) -> str:
    """R5 AC8: the build every shard must come from.

    ``DOCLING_EXPECTED_BUILD_SHA`` when set; else the build of the backend the
    controller routes ``docling-active`` to (what an unsplit conversion would
    use); else the fastest backend's. The worker's own ``BUILD_SHA`` is not
    usable: the worker image is rebuilt on every merge, docling-service only
    when its code changes, so the two differ most of the time.
    """
    if settings.docling_expected_build_sha:
        return settings.docling_expected_build_sha
    if not caps:
        return ""
    from ..cache import get_docling_backend_state

    state = await get_docling_backend_state() or {}
    target = state.get("target")
    if target in caps:
        return caps[target].build_sha
    fastest = max(caps, key=lambda n: (caps[n].rate, n))
    return caps[fastest].build_sha


async def _eligible_backends(client) -> tuple[dict[str, tuple[SplitBackend, Capacity]], str]:
    """Eligible backends and the expected build. Every configured backend gets
    one ``docling_split_backend`` record; one still cached as absent is
    reported ``unreachable`` without a new probe."""
    backends = configured_backends()

    async def _probe(b: SplitBackend) -> Capacity | None:
        return None if _is_absent(b.url) else await _fetch_capacity(client, b)

    snaps = await asyncio.gather(*(_probe(b) for b in backends))
    answered = {b.name: c for b, c in zip(backends, snaps, strict=True) if c is not None}
    expected = await _expected_build_sha(answered)
    eligible: dict[str, tuple[SplitBackend, Capacity]] = {}
    for b, cap in zip(backends, snaps, strict=True):
        if cap is None:
            choice = "unreachable"
        elif not hr3_eligible(
            b.name, pii_corpus=settings.pii_corpus, url=b.url, reported=cap.backend
        ):
            choice = "hr3_blocked"
        elif not build_matches(expected, cap.build_sha):
            choice = "build_skew"
            logger.warning(
                "split: excluding backend %s: build_sha %s != expected %s",
                b.name,
                cap.build_sha,
                expected,
            )
        elif cap.safe_procs < 1:
            choice = "no_capacity"
        elif not cap.has_free_slot:
            choice = "busy"
        else:
            choice = "eligible"
            eligible[b.name] = (b, cap)
        decision(
            event="docling_split_backend",
            choice=choice,
            reason=b.name,
            attrs={
                "backend": b.name,
                "build_sha": cap.build_sha if cap else None,
                "expected_sha": expected or None,
                "safe_procs": cap.safe_procs if cap else None,
                "busy_slots": cap.busy_slots if cap else None,
                "max_slots": cap.max_slots if cap else None,
                "spp_ewma": cap.spp_ewma if cap else None,
            },
        )
    return eligible, expected


def _fmt_counts(counts: dict[str, int]) -> str:
    return ",".join(f"{k}:{v}" for k, v in sorted(counts.items()))


async def split_convert(
    staging_key: str,
    *,
    page_count: int | None,
    page_classes: list | None,
    convert_kwargs: dict,
):
    """Convert across the eligible backends, or return ``None`` for today's path.

    ``convert_kwargs`` are the caller's other ``_remote_pdf_convert`` keyword
    arguments, whole-document values; the service slices them per shard.
    """
    import httpx

    from .remote import (
        _MIN_USEFUL_CALL_S,
        DoclingUnavailable,
        _check_remote_docling_version,
        _effective_read_timeout_s,
        _remote_pdf_convert,
        merge_convert_results,
    )

    if not settings.docling_split_enabled:
        return None
    if not isinstance(page_count, int) or page_count < max(2, settings.docling_split_min_pages):
        return None
    read_s = _effective_read_timeout_s()
    if read_s is None:
        return None  # today's path raises its usual DoclingUnavailable
    t0 = time.monotonic()
    deadline = t0 + read_s

    async with httpx.AsyncClient(timeout=_CAPACITY_TIMEOUT_S) as client:
        eligible, expected_sha = await _eligible_backends(client)
    if eligible:
        # The pipeline-version gate (REMOTE_VERSION_ENFORCE, DOCLING_VERSION_SKEW)
        # still runs once per process against docling-active. Every shard's
        # build was matched above to the expected build, which is
        # docling-active's unless DOCLING_EXPECTED_BUILD_SHA overrides it or
        # the controller's target did not answer.
        async with httpx.AsyncClient(timeout=10.0) as client:
            await _check_remote_docling_version(client)

    def _emit(choice: str, **attrs) -> None:
        attrs.setdefault("backends", ",".join(sorted(eligible)))
        attrs["elapsed_ms"] = int((time.monotonic() - t0) * 1000)
        decision(event="docling_split", choice=choice, reason=choice, attrs=attrs)

    if not eligible:
        _emit("fallback_active")
        return None
    if len(eligible) == 1:
        backend = next(iter(eligible.values()))[0]
        res = await _remote_pdf_convert(
            staging_key,
            page_count=page_count,
            page_classes=page_classes,
            base_url=backend.url,
            hr3_checked=True,
            **convert_kwargs,
        )
        _emit("single_backend", shard_count=1, pages_by_backend=f"{backend.name}:{page_count}")
        return res

    caps = {n: c for n, (_, c) in eligible.items()}
    chunks = doc_chunks(page_count, page_classes, min(c.chunk_pages for c in caps.values()))
    initial, tail = plan_split(chunks, caps, initial_frac=settings.docling_split_initial_frac)
    n_shards = len(initial) + len(tail)

    first_dispatch = dict(initial)
    queue: list[Shard] = list(tail)
    # (shard, attempts so far, backend it last failed on)
    retry: list[tuple[Shard, int, str]] = []
    attempts: dict[int, int] = {}
    results = []
    pages_by: dict[str, int] = {}
    shards_by: dict[str, int] = {}
    retries = 0
    reroutes = 0
    live = set(eligible)
    idle_until: dict[str, float] = {}
    inflight: dict[asyncio.Future, tuple[str, Shard]] = {}

    async def _run(name: str, shard: Shard):
        remaining = deadline - time.monotonic()
        if remaining < _MIN_USEFUL_CALL_S:
            raise DoclingUnavailable(
                f"split deadline: {remaining:.0f}s left for shard {shard.index + 1}/{n_shards}"
            )
        return await _remote_pdf_convert(
            staging_key,
            page_count=page_count,
            page_classes=page_classes,
            page_start=shard.start,
            page_end=shard.end,
            base_url=eligible[name][0].url,
            read_timeout_s=remaining,
            shard=f"{shard.index + 1}/{n_shards}:{shard.start}-{shard.end}",
            hr3_checked=True,
            **convert_kwargs,
        )

    def _next_for(name: str) -> tuple[Shard, int, bool, str] | None:
        """``name``'s next shard as ``(shard, attempts, is_initial, failed_on)``:
        its initial block, then a retry that failed elsewhere, then a tail
        shard, then -- only when no other backend is live -- a retry that
        failed on ``name`` itself."""
        if name in first_dispatch:
            return first_dispatch.pop(name), 0, True, ""
        for i, (shard, n, failed_on) in enumerate(retry):
            if failed_on != name:
                retry.pop(i)
                return shard, n, False, failed_on
        if queue:
            return queue.pop(0), 0, False, ""
        if not any(b != name for b in live):
            for i, (shard, n, failed_on) in enumerate(retry):
                retry.pop(i)
                return shard, n, False, failed_on
        return None

    async def _still_accepts(name: str) -> bool:
        """Per-shard check before a stolen or retried shard (design). A
        backend that stopped answering, or now runs a different build than the
        rest of this document's shards (a restart or rollout between probes),
        leaves the split for good."""
        async with httpx.AsyncClient(timeout=_CAPACITY_TIMEOUT_S) as client:
            cap = await _fetch_capacity(client, eligible[name][0])
        if cap is None:
            live.discard(name)
            return False
        if not build_matches(expected_sha, cap.build_sha):
            logger.warning(
                "split: dropping backend %s mid-document: build_sha %s != expected %s",
                name,
                cap.build_sha,
                expected_sha,
            )
            live.discard(name)
            return False
        return cap.accepts_work

    last_error: BaseException | None = None
    try:
        while first_dispatch or queue or retry or inflight:
            busy = {n for n, _ in inflight.values()}
            for name in sorted(live - busy):
                if idle_until.get(name, 0.0) > time.monotonic():
                    continue
                item = _next_for(name)
                if item is None:
                    continue
                shard, n, is_initial, failed_on = item
                if not is_initial and not await _still_accepts(name):
                    # Not queued behind a busy backend (R5 AC6): put it back,
                    # still marked with the backend it failed on, so the retry
                    # keeps preferring another backend.
                    if n:
                        retry.insert(0, (shard, n, failed_on))
                    else:
                        queue.insert(0, shard)
                    idle_until[name] = time.monotonic() + _BUSY_POLL_S
                    continue
                attempts[shard.index] = n + 1
                inflight[asyncio.ensure_future(_run(name, shard))] = (name, shard)
            if not inflight:
                if not live:
                    raise last_error or DoclingUnavailable("split: no live backend left")
                if time.monotonic() >= deadline:
                    raise last_error or DoclingUnavailable(
                        "split: deadline with shards undispatched"
                    )
                await asyncio.sleep(_BUSY_POLL_S)
                continue
            done, _ = await asyncio.wait(
                inflight, timeout=_BUSY_POLL_S, return_when=asyncio.FIRST_COMPLETED
            )
            for fut in done:
                name, shard = inflight.pop(fut)
                exc = fut.exception()
                if exc is None:
                    results.append(fut.result())
                    pages_by[name] = pages_by.get(name, 0) + shard.pages
                    shards_by[name] = shards_by.get(name, 0) + 1
                    continue
                last_error = exc
                if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
                    _mark_absent(eligible[name][0].url)
                    live.discard(name)
                if attempts[shard.index] >= 2 or isinstance(exc, DoclingUnavailable):
                    raise exc
                retries += 1
                if any(b != name for b in live):
                    reroutes += 1
                logger.warning(
                    "split: shard %d/%d (pages %d-%d) failed on %s (%s); retrying once",
                    shard.index + 1,
                    n_shards,
                    shard.start,
                    shard.end,
                    name,
                    type(exc).__name__,
                )
                retry.append((shard, attempts[shard.index], name))
    except BaseException as e:
        for fut in inflight:
            fut.cancel()
        if inflight:
            await asyncio.gather(*inflight, return_exceptions=True)
        _emit(
            "failed",
            shard_count=n_shards,
            pages_by_backend=_fmt_counts(pages_by),
            shards_by_backend=_fmt_counts(shards_by),
            retries=retries,
            reroutes=reroutes,
            error_class=type(e).__name__,
        )
        raise

    ordered = sorted(results, key=lambda r: r.page_start)
    _emit(
        "split",
        shard_count=n_shards,
        pages_by_backend=_fmt_counts(pages_by),
        shards_by_backend=_fmt_counts(shards_by),
        retries=retries,
        reroutes=reroutes,
        split_join_heading_shifts=join_heading_shifts([r.markdown for r in ordered]),
    )
    return merge_convert_results(results)
