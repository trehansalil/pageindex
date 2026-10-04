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
3. Split (A-P5-5): the document is cut into small chunks, page-class aligned,
   about two per slot, in one shared queue. Each backend keeps up to
   ``chunk_limit`` chunks in flight (its slice slots, capped by
   ``safe_procs``) and takes the next chunk when one finishes, so measured
   speed, not a prior, decides who converts what. Once the queue is empty an
   idle backend also runs a copy of the oldest chunk still running elsewhere;
   the first result wins and the other is cancelled. Every chunk shares one
   presigned URL, so each backend downloads the PDF once.
4. One shared deadline for all chunks. A failed chunk is retried once, on
   another backend when one is live; a second failure fails the conversion
   with the chunk's own exception, so the worker's retry policy is unchanged.
5. Merge in page order and count heading jumps at chunk joins (R6 AC2).
"""

from __future__ import annotations

import asyncio
import dataclasses
import logging
import math
import re
import time

from ..config import settings
from ..obs.decisions import decision

logger = logging.getLogger(__name__)

# Backends that refused a connection are skipped for this long (design:
# "a backend that refuses connections is marked absent (cached 60 s)").
_ABSENT_TTL_S = 60.0
_CAPACITY_TIMEOUT_S = 2.0
# The scheduler's tick while nothing it waits on has finished.
_BUSY_POLL_S = 5.0
# Smallest split chunk. Each chunk is one process on a share of the cores, so
# its model load (~5-10 s) is small against even 5 pages at ~10-20 s each.
_MIN_SPLIT_CHUNK_PAGES = 5
# Presigned URLs live 15 min (storage.presigned_get_url); renew well before.
_PRESIGN_REFRESH_S = 600.0
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
    # A-P5-5: split chunks the service runs at once; 0 for a build that runs
    # one request at a time.
    slice_slots: int = 0

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
            slice_slots=max(0, _num("slice_slots", 0, int)),
        )

    @property
    def chunk_limit(self) -> int:
        """Chunks to keep in flight on this backend: its slice slots, capped by
        the processes its free memory allows; 1 for an older build."""
        if self.slice_slots < 1:
            return 1
        return max(1, min(self.safe_procs, self.slice_slots))

    @property
    def rate(self) -> float:
        """Pages per second this backend converts (``safe_procs / spp_ewma``)."""
        return self.safe_procs / self.spp_ewma

    @property
    def has_free_slot(self) -> bool:
        return self.busy_slots < self.max_slots


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
    """A split's chunks, ``(start, end)`` inclusive, at most ``chunk_pages`` each.

    With usable page classes they are R3 page-class chunks (needs-uniform, so
    each chunk keeps its own OCR/TableFormer choice); without, uniform
    ``chunk_pages`` runs. Each chunk is one request and one process (A-P5-5).
    """
    from ..converters.docling_conv import _page_classes_active
    from ..converters.docling_resources import MIN_CHUNK_PAGES
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
                classes,
                max_pages=max(1, chunk_pages),
                min_pages=min(MIN_CHUNK_PAGES, max(1, chunk_pages)),
            )
        ]
    step = max(1, chunk_pages)
    return [(s, min(s + step, page_count) - 1) for s in range(0, page_count, step)]


def split_chunk_pages(page_count: int, total_slots: int) -> int:
    """Pages per chunk: about two chunks per slot across the backends, so a
    fast backend keeps pulling work while a slow one finishes its last chunk,
    within ``[_MIN_SPLIT_CHUNK_PAGES, SLICE_MAX_PAGES]`` (the service runs a
    larger range as a whole PDF)."""
    from ..converters.docling_resources import SLICE_MAX_PAGES

    target = math.ceil(page_count / max(1, 2 * total_slots))
    return max(_MIN_SPLIT_CHUNK_PAGES, min(SLICE_MAX_PAGES, target))


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

    from ..storage import presigned_get_url

    caps = {n: c for n, (_, c) in eligible.items()}
    limit = {n: c.chunk_limit for n, c in caps.items()}
    size = split_chunk_pages(page_count, sum(limit.values()))
    shards = [Shard(i, s, e) for i, (s, e) in enumerate(doc_chunks(page_count, page_classes, size))]
    n_shards = len(shards)

    queue: list[int] = list(range(n_shards))  # chunk indexes not started yet
    retry: list[tuple[int, str]] = []  # (chunk index, backend it failed on)
    failures: dict[int, int] = {}
    results: dict[int, object] = {}
    # Insertion order is dispatch order: the first entry is the oldest chunk.
    running: dict[asyncio.Future, tuple[str, int, str]] = {}  # (backend, index, kind)
    copied: set[int] = set()
    pages_by: dict[str, int] = {}
    shards_by: dict[str, int] = {}
    counts = {"retries": 0, "reroutes": 0, "copies": 0, "copy_wins": 0}
    live = set(eligible)
    # One presigned URL for every chunk, so each backend downloads the PDF
    # once (the service caches by URL); renewed before it can expire.
    presigned = [presigned_get_url(staging_key), time.monotonic()]

    def _url() -> str:
        if time.monotonic() - presigned[1] > _PRESIGN_REFRESH_S:
            presigned[:] = [presigned_get_url(staging_key), time.monotonic()]
        return presigned[0]

    async def _run(name: str, idx: int):
        shard = shards[idx]
        remaining = deadline - time.monotonic()
        if remaining < _MIN_USEFUL_CALL_S:
            raise DoclingUnavailable(
                f"split deadline: {remaining:.0f}s left for chunk {idx + 1}/{n_shards}"
            )
        return await _remote_pdf_convert(
            staging_key,
            page_count=page_count,
            page_classes=page_classes,
            page_start=shard.start,
            page_end=shard.end,
            base_url=eligible[name][0].url,
            read_timeout_s=remaining,
            shard=f"{idx + 1}/{n_shards}:{shard.start}-{shard.end}",
            hr3_checked=True,
            presigned_url=_url(),
            **convert_kwargs,
        )

    def _pick(name: str) -> tuple[int, str, str] | None:
        """``name``'s next chunk as ``(index, kind, failed_on)``: a retry that
        failed elsewhere, then the queue, then -- only when no other backend
        is live -- a retry that failed on ``name`` itself, then a copy of the
        oldest chunk still running on another backend (the tail)."""
        for i, (idx, failed_on) in enumerate(retry):
            if failed_on != name:
                retry.pop(i)
                return idx, "retry", failed_on
        if queue:
            return queue.pop(0), "new", ""
        if retry and not any(b != name for b in live):
            idx, failed_on = retry.pop(0)
            return idx, "retry", failed_on
        for other, idx, _ in running.values():
            if other != name and idx not in copied and idx not in results:
                copied.add(idx)
                return idx, "copy", ""
        return None

    async def _still_accepts(name: str) -> bool:
        """Checked before a retried or copied chunk (design). A backend that
        stopped answering, or now runs a different build than the rest of this
        document's chunks (a restart or rollout between probes), leaves the
        split for good. Its own chunks make it look busy, so busy is not
        checked here."""
        async with httpx.AsyncClient(timeout=_CAPACITY_TIMEOUT_S) as client:
            cap = await _fetch_capacity(client, eligible[name][0])
        if cap is not None and build_matches(expected_sha, cap.build_sha):
            return True
        if cap is not None:
            logger.warning(
                "split: dropping backend %s mid-document: build_sha %s != expected %s",
                name,
                cap.build_sha,
                expected_sha,
            )
        live.discard(name)
        return False

    def _dispatch_order() -> list[str]:
        return sorted(live, key=lambda n: (-caps[n].rate, n))

    last_error: BaseException | None = None
    try:
        while len(results) < n_shards:
            for name in _dispatch_order():
                while (
                    name in live
                    and sum(1 for b, _, _ in running.values() if b == name) < limit[name]
                ):
                    item = _pick(name)
                    if item is None:
                        break
                    idx, kind, failed_on = item
                    if kind != "new" and not await _still_accepts(name):
                        if kind == "retry":
                            retry.insert(0, (idx, failed_on))
                        else:
                            copied.discard(idx)
                        break
                    if kind == "copy":
                        counts["copies"] += 1
                    running[asyncio.ensure_future(_run(name, idx))] = (name, idx, kind)
            if not running:
                if not live:
                    raise last_error or DoclingUnavailable("split: no live backend left")
                if time.monotonic() >= deadline:
                    raise last_error or DoclingUnavailable(
                        "split: deadline with chunks undispatched"
                    )
                await asyncio.sleep(_BUSY_POLL_S)
                continue
            done, _ = await asyncio.wait(
                running, timeout=_BUSY_POLL_S, return_when=asyncio.FIRST_COMPLETED
            )
            for fut in done:
                name, idx, kind = running.pop(fut)
                if fut.cancelled() or idx in results:
                    continue  # the other copy already won
                exc = fut.exception()
                if exc is None:
                    results[idx] = fut.result()
                    pages_by[name] = pages_by.get(name, 0) + shards[idx].pages
                    shards_by[name] = shards_by.get(name, 0) + 1
                    if kind == "copy":
                        counts["copy_wins"] += 1
                    for other_fut, (_, j, _) in running.items():
                        if j == idx:
                            other_fut.cancel()  # the client disconnect cancels it remotely
                    continue
                last_error = exc
                if isinstance(exc, (httpx.ConnectError, httpx.ConnectTimeout)):
                    _mark_absent(eligible[name][0].url)
                    live.discard(name)
                if isinstance(exc, DoclingUnavailable):
                    raise exc
                if any(j == idx for _, j, _ in running.values()):
                    continue  # its other copy may still finish
                failures[idx] = failures.get(idx, 0) + 1
                if failures[idx] >= 2:
                    raise exc
                counts["retries"] += 1
                if any(b != name for b in live):
                    counts["reroutes"] += 1
                logger.warning(
                    "split: chunk %d/%d (pages %d-%d) failed on %s (%s); retrying once",
                    idx + 1,
                    n_shards,
                    shards[idx].start,
                    shards[idx].end,
                    name,
                    type(exc).__name__,
                )
                retry.append((idx, name))
    except BaseException as e:
        _emit(
            "failed",
            shard_count=n_shards,
            chunk_pages=size,
            slots_by_backend=_fmt_counts(limit),
            pages_by_backend=_fmt_counts(pages_by),
            shards_by_backend=_fmt_counts(shards_by),
            error_class=type(e).__name__,
            **counts,
        )
        raise
    finally:
        for fut in running:
            fut.cancel()
        if running:
            await asyncio.gather(*running, return_exceptions=True)

    ordered = [results[i] for i in range(n_shards)]
    _emit(
        "split",
        shard_count=n_shards,
        chunk_pages=size,
        slots_by_backend=_fmt_counts(limit),
        pages_by_backend=_fmt_counts(pages_by),
        shards_by_backend=_fmt_counts(shards_by),
        split_join_heading_shifts=join_heading_shifts([r.markdown for r in ordered]),
        **counts,
    )
    return merge_convert_results(ordered)
