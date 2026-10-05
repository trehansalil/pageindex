"""Worker-side table capture (RFC-052 R7 AC1-3, task 9.1).

``start()`` returns at once; a supervisor thread acquires pod slots, spawns
one ``spawn``-context process per contiguous page range, drains the per-page
stream, and SIGKILLs any process whose RSS passes ``TABLES_PROC_BYTES``.
Dispatch of the remote conversion never waits on any of it (P6).

Pool size (per document, P7)::

    cpu   = floor(available_cpus())
    mem   = floor((free_memory_bytes() - TABLES_RESERVE_BYTES) / TABLES_PROC_BYTES)
    pages = ceil(N / TABLES_MIN_PAGES_PER_PROC)
    procs = max(1, min(cpu, mem, pages, slots_free))

``slots_free`` is the number of ``TABLES_POD_SLOTS`` fcntl lock files this
document manages to hold, so capture processes across every concurrent job in
the pod never exceed ``TABLES_POD_SLOTS``. The first slot is waited for up to
the deadline; without one, every page is ``no_slot``.

``ProcessPoolExecutor`` is deliberately not used: one killed worker breaks the
whole pool (``BrokenProcessPool``) and loses which task died.

Capture failure is never a job error: ``CaptureHandle.join`` never raises.
Nothing here touches ``validate_tree`` or the gate (HR5).
"""

from __future__ import annotations

import asyncio
import contextlib
import fcntl
import logging
import math
import multiprocessing
import os
import signal
import threading
import time
from collections.abc import Sequence
from dataclasses import dataclass, field
from typing import Any

from ..converters.docling_resources import available_cpus, free_memory_bytes
from ..converters.preclassify import PageClass
from . import _capture_child
from .schema import CaptureResult, FailedRange, TableRecord
from .settings import CaptureSettings, capture_settings

logger = logging.getLogger(__name__)

__all__ = ["CaptureHandle", "CaptureResult", "pool_size", "split_ranges", "start"]

SLOT_LOCK_TEMPLATE = "/tmp/pageindex-tables-slot-{i}.lock"
_SLOT_WAIT_POLL_S = 0.5
_PAGE_SIZE = os.sysconf("SC_PAGE_SIZE") if hasattr(os, "sysconf") else 4096
# The process target; a module attribute so a test can substitute a hog.
_child_target = _capture_child.run_range


# ---------------------------------------------------------------- pure helpers


def pool_size(  # noqa: PLR0913 -- one argument per term of the design formula
    n_pages: int,
    *,
    cpus: int,
    free_bytes: int | None,
    reserve_bytes: int,
    proc_bytes: int,
    min_pages_per_proc: int,
    slots_free: int,
) -> int:
    """``max(1, min(cpu, mem, pages, slots_free))``. Unreadable free memory
    counts as room for one process, never a guess upward."""
    mem = 1 if free_bytes is None else (free_bytes - reserve_bytes) // max(1, proc_bytes)
    pages = math.ceil(max(0, n_pages) / max(1, min_pages_per_proc))
    return max(1, min(int(cpus), int(mem), pages, int(slots_free)))


def split_ranges(n_pages: int, procs: int) -> list[tuple[int, int]]:
    """``procs`` contiguous, balanced, inclusive ranges partitioning ``[0, N)``."""
    procs = max(1, min(procs, n_pages))
    base, extra = divmod(n_pages, procs)
    out, start = [], 0
    for i in range(procs):
        size = base + (1 if i < extra else 0)
        out.append((start, start + size - 1))
        start += size
    return out


def scan_order(start: int, end: int, page_classes: Sequence[PageClass] | None) -> list[int]:
    """Pages of ``[start, end]`` with P1 table positives first (cheap
    positives first), each group in page order."""
    pages = list(range(start, end + 1))
    if not page_classes:
        return pages

    def positive(p: int) -> bool:
        return p < len(page_classes) and page_classes[p].has_tables

    return [p for p in pages if positive(p)] + [p for p in pages if not positive(p)]


def failed_runs(pages: Sequence[int], reason: str) -> list[FailedRange]:
    """Contiguous inclusive runs of *pages*, each tagged with *reason*."""
    runs: list[FailedRange] = []
    for p in sorted(set(pages)):
        if runs and runs[-1][1] == p - 1:
            runs[-1] = (runs[-1][0], p, reason)
        else:
            runs.append((p, p, reason))
    return runs


def rss_bytes(pid: int) -> int | None:
    """Resident set size from ``/proc/<pid>/statm`` (2nd field, pages)."""
    try:
        with open(f"/proc/{pid}/statm") as fh:
            return int(fh.read().split()[1]) * _PAGE_SIZE
    except (OSError, ValueError, IndexError):
        return None


# ---------------------------------------------------------------- pod slots


def _try_slot(i: int) -> int | None:
    path = SLOT_LOCK_TEMPLATE.format(i=i)
    fd = os.open(path, os.O_RDWR | os.O_CREAT, 0o600)
    try:
        fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        os.close(fd)
        return None
    return fd


def acquire_slots(
    want: int, pod_slots: int, *, deadline: float, stop: threading.Event | None = None
) -> list[int]:
    """Hold up to *want* of the pod's slot locks. Waits (until *deadline* or
    *stop*) for the first one only; the rest are taken if free right now.
    Returns the held file descriptors (release with ``release_slots``)."""
    held: list[int] = []
    while True:
        for i in range(pod_slots):
            if len(held) >= want:
                break
            fd = _try_slot(i)
            if fd is not None:
                held.append(fd)
        if held or time.monotonic() >= deadline or (stop is not None and stop.is_set()):
            return held
        time.sleep(_SLOT_WAIT_POLL_S)


def release_slots(fds: Sequence[int]) -> None:
    for fd in fds:
        with contextlib.suppress(OSError):
            fcntl.flock(fd, fcntl.LOCK_UN)
        with contextlib.suppress(OSError):
            os.close(fd)


# ---------------------------------------------------------------- supervisor


@dataclass
class _Worker:
    pages: list[int]
    proc: Any
    q: Any  # the read end of the process's pipe
    done: set[int] = field(default_factory=set)
    finished: bool = False
    reason: str | None = None  # why unfinished pages failed


class _Capture:
    def __init__(
        self,
        pdf_path: str,
        page_count: int,
        page_classes: Sequence[PageClass] | None,
        deadline: float,
        cfg: CaptureSettings,
    ) -> None:
        self.pdf_path = pdf_path
        self.n = page_count
        self.page_classes = page_classes
        self.deadline = deadline
        self.cfg = cfg
        self.stop = threading.Event()
        self.result: CaptureResult | None = None
        self.t0 = time.monotonic()
        self.thread = threading.Thread(target=self._run, name="tables-capture", daemon=True)

    # All pages failed with one reason (no slot, or a supervisor crash).
    def _all_failed(self, reason: str, procs: int = 0) -> CaptureResult:
        return CaptureResult(
            tables=[],
            failed=failed_runs(range(self.n), reason),
            procs=procs,
            duration_s=time.monotonic() - self.t0,
            peak_rss_bytes=0,
        )

    def _run(self) -> None:
        try:
            self.result = self._supervise()
        except Exception:
            logger.warning("table capture supervisor failed", exc_info=True)
            self.result = self._all_failed("crash")

    def _supervise(self) -> CaptureResult:
        cfg = self.cfg
        want = pool_size(
            self.n,
            cpus=available_cpus(),
            free_bytes=free_memory_bytes(),
            reserve_bytes=cfg.reserve_bytes,
            proc_bytes=cfg.proc_bytes,
            min_pages_per_proc=cfg.min_pages_per_proc,
            slots_free=cfg.pod_slots,
        )
        if self.n <= 0:
            return CaptureResult([], [], 0, time.monotonic() - self.t0, 0)
        slots = acquire_slots(want, cfg.pod_slots, deadline=self.deadline, stop=self.stop)
        if not slots:
            logger.warning("table capture: no pod slot before the deadline")
            return self._all_failed("no_slot")
        try:
            return self._run_pool(slots)
        finally:
            release_slots(slots)

    def _run_pool(self, slots: list[int]) -> CaptureResult:
        """Scan ranges of ``TABLES_MIN_PAGES_PER_PROC`` pages from a queue, one
        process per range. The pool starts at the slots held and grows (more
        slots, re-checked CPU and memory) as room frees up, e.g. once the
        converter child exits, so coverage depends on the deadline, not on
        how much memory was free when the document started."""
        ctx = multiprocessing.get_context("spawn")
        queue = list(split_ranges(self.n, math.ceil(self.n / self.cfg.min_pages_per_proc)))
        workers: list[_Worker] = []
        records: list[TableRecord] = []
        peak = 0
        procs = 0  # most processes alive at once
        try:
            while True:
                for w in workers:
                    peak = max(peak, self._check(w, records))
                running = [w for w in workers if not (w.finished or w.reason is not None)]
                if self.stop.is_set() or time.monotonic() >= self.deadline:
                    for w in running:
                        self._kill(w, "deadline")
                    break
                for _ in range(self._room(running, slots, len(queue)) if queue else 0):
                    start, end = queue.pop(0)
                    workers.append(self._spawn(ctx, scan_order(start, end, self.page_classes)))
                    running.append(workers[-1])
                procs = max(procs, len(running))
                if not running and not queue:
                    break
                time.sleep(self.cfg.rss_poll_s)
        finally:
            for w in workers:
                if w.proc.is_alive():
                    self._kill(w, w.reason or "deadline")
            # Reap reliably: a single 1s join can return while the kernel
            # is still tearing the SIGKILLed process down, leaving a zombie
            # behind. Keep polling (bounded) until each is actually gone.
            # One 5 s budget for the whole pool, not per worker: all were
            # killed above, so a large pool still finishes well inside
            # join's 30 s fallback.
            reap_deadline = time.monotonic() + 5.0
            for w in workers:
                with contextlib.suppress(Exception):
                    while w.proc.is_alive() and time.monotonic() < reap_deadline:
                        w.proc.join(0.2)
                peak = max(peak, self._drain(w, records))  # pages sent before the kill
                with contextlib.suppress(Exception):
                    w.q.close()
        # Pages per reason first, so adjacent ranges join into one run.
        lost: dict[str, list[int]] = {}
        for w in workers:
            if not w.finished:
                todo = [p for p in w.pages if p not in w.done]
                lost.setdefault(w.reason or "crash", []).extend(todo)
        for start, end in queue:  # never started: the deadline came first
            lost.setdefault("deadline", []).extend(range(start, end + 1))
        failed: list[FailedRange] = [
            run for reason, pages in lost.items() for run in failed_runs(pages, reason)
        ]
        return CaptureResult(
            tables=sorted(records, key=lambda r: (r.page, r.table_id)),
            failed=sorted(failed),
            procs=procs,
            duration_s=time.monotonic() - self.t0,
            peak_rss_bytes=peak,
        )

    def _spawn(self, ctx: Any, pages: list[int]) -> _Worker:
        # One one-way pipe per process: with the parent's write end
        # closed, a message torn by a SIGKILL reads as EOFError, not a hang.
        reader, writer = ctx.Pipe(duplex=False)
        proc = ctx.Process(
            target=_child_target,
            args=(writer, self.pdf_path, pages, tuple(self.cfg.strategies)),
            daemon=True,
        )
        proc.start()
        writer.close()
        return _Worker(pages=pages, proc=proc, q=reader)

    def _room(self, running: list[_Worker], slots: list[int], pending: int) -> int:
        """How many more processes may start now. Each running process is
        counted at ``TABLES_PROC_BYTES`` (the most it may grow to before its
        kill), not its current RSS, so a just-spawned child is never
        double-booked. Takes extra pod slots when free; always lets one run."""
        cfg = self.cfg
        free = free_memory_bytes()
        if free is None:
            mem = 0
        else:
            grown = sum(max(0, cfg.proc_bytes - (rss_bytes(w.proc.pid) or 0)) for w in running)
            mem = (free - grown - cfg.reserve_bytes) // max(1, cfg.proc_bytes)
        room = min(int(available_cpus()) - len(running), int(mem))
        if not running:
            room = max(1, room)
        room = min(room, cfg.pod_slots, pending)
        if len(running) + room > len(slots):
            slots.extend(acquire_slots(len(running) + room - len(slots), cfg.pod_slots, deadline=0))
        return max(0, min(room, len(slots) - len(running)))

    def _check(self, w: _Worker, records: list[TableRecord]) -> int:
        """One watchdog tick for *w*: drain its pages, SIGKILL it over
        ``TABLES_PROC_BYTES`` (P8), notice a silent death. Returns peak RSS seen."""
        peak = self._drain(w, records)
        if w.finished or w.reason is not None:
            return peak
        rss = rss_bytes(w.proc.pid)
        if rss is not None:
            peak = max(peak, rss)
            if rss > self.cfg.proc_bytes:
                self._kill(w, "rss_limit")
                logger.warning(
                    "table capture: pid %s RSS %d > TABLES_PROC_BYTES %d, killed",
                    w.proc.pid,
                    rss,
                    self.cfg.proc_bytes,
                )
                return peak
        if not w.proc.is_alive():
            w.proc.join(0.1)
            peak = max(peak, self._drain(w, records))
            if not w.finished:
                w.reason = w.reason or "crash"
        return peak

    @staticmethod
    def _drain(w: _Worker, records: list[TableRecord]) -> int:
        """Read every queued message; returns the peak RSS a ``done`` reported."""
        peak = 0
        while True:
            try:
                if w.q.closed or not w.q.poll():
                    return peak
                msg = w.q.recv()
            except (EOFError, OSError):
                return peak  # child gone; a torn last message is simply lost
            except Exception:
                return peak
            kind = msg[0]
            if kind == "page":
                w.done.add(int(msg[1]))
                records.extend(TableRecord.from_dict(d) for d in msg[2])
            elif kind == "done":
                w.finished = set(w.pages) <= w.done
                peak = max(peak, int(msg[1] or 0))
            elif kind == "error":
                # Do NOT settle the worker here: the child keeps scanning the
                # rest of its range after one bad page (_capture_child.py's
                # per-page try/except), so treating this as "settled" would
                # let the main loop break and the `finally` SIGKILL the
                # still-running child -- turning one bad page into every
                # later, would-have-succeeded page being marked "crash" too.
                # The dead-process path in `_check` (once the child actually
                # exits with this page still missing from `w.done`) is what
                # tags the worker "crash" -- correctly scoped to just the
                # page(s) that errored.
                logger.warning("table capture: page failed: %s", msg[1])

    @staticmethod
    def _kill(w: _Worker, reason: str) -> None:
        w.reason = w.reason or reason
        with contextlib.suppress(ProcessLookupError, OSError):
            os.kill(w.proc.pid, signal.SIGKILL)


class CaptureHandle:
    """Returned by ``start``; ``join`` collects the result."""

    def __init__(
        self, capture: _Capture | None = None, *, immediate_result: CaptureResult | None = None
    ) -> None:
        self._c = capture
        self._immediate = immediate_result

    def stop(self) -> None:
        """Ask the capture to wind down now (its unfinished pages become
        ``deadline``); ``join`` still collects what it found. Never raises."""
        if self._c is not None:
            self._c.stop.set()

    async def join(self, grace_s: float | None) -> CaptureResult:
        """Wait up to *grace_s* for capture, then kill stragglers (their
        unfinished pages become ``deadline``). ``None`` waits for the capture
        to finish or reach its own deadline, so how many pages it covers does
        not depend on how fast the conversion was. Never raises."""
        if self._immediate is not None:
            return self._immediate
        c = self._c
        assert c is not None
        if grace_s is None:
            # The supervisor checks its deadline every poll and reaps for up
            # to 5 s per process after; the 30 s join below covers that tail.
            grace_s = c.deadline - time.monotonic()
        try:
            await asyncio.to_thread(c.thread.join, max(0.0, grace_s))
            if c.thread.is_alive():
                c.stop.set()
                await asyncio.to_thread(c.thread.join, 30.0)
            if c.result is not None:
                return c.result
        except Exception:
            logger.warning("table capture join failed", exc_info=True)
        c.stop.set()
        return c._all_failed("deadline")


def start(
    pdf_path: str,
    *,
    page_count: int,
    page_classes: list[PageClass] | None,
    deadline_monotonic: float,
) -> CaptureHandle:
    """Begin capturing *pdf_path* in the background and return immediately."""
    cfg = capture_settings()
    if not cfg.enabled:
        # TABLES_CAPTURE=0: no process, no failures -- just nothing captured.
        return CaptureHandle(
            immediate_result=CaptureResult(
                tables=[], failed=[], procs=0, duration_s=0.0, peak_rss_bytes=0
            )
        )
    capture = _Capture(pdf_path, page_count, page_classes, deadline_monotonic, cfg)
    capture.thread.start()
    return CaptureHandle(capture)
