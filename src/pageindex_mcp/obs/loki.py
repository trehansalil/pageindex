"""In-process Loki push (RFC-052 task 1.10; replaces the Mac's Grafana Alloy).

When ``PAGEINDEX_LOKI_PUSH_URL`` is set, ``configure()`` installs a
``LokiPushHandler`` next to the stderr / ``PAGEINDEX_LOG_FILE`` handler. It
ships the same obs JSON line the local handler writes to Loki's JSON push API
(``/loki/api/v1/push``) from a background daemon thread, so a host needs no
log agent, no brew and no sudo -- only the env var.

Contract:
  - ``emit`` never blocks and never raises: records go into a bounded deque;
    when it is full the OLDEST line is dropped and counted (``dropped``).
  - Batches go out at ``batch_size`` lines or every ``flush_interval`` s.
  - Stream labels stay low-cardinality: ``host``, ``service``, ``level``,
    ``kind``. ``job_id`` / ``doc_id`` / ``doc_sha8`` / ``run_id`` travel as
    Loki 3 structured metadata (the 3rd element of each value), never labels.
    They are also in the JSON line itself, so Loki is an HR2 derived store:
    ``request_log_deletion`` below is the erasure half, driven by
    ``storage.documents.delete_doc`` (step ``loki_logs``) against the
    in-cluster Loki -- the Tailscale gateway stays push-only.
  - A retryable failure (network error, 5xx, 429) is retried with exponential
    backoff; a 4xx rejection drops the batch. Failures are reported on stderr
    at most once a minute, never through ``logging`` (no recursion).
  - ``close()`` -- run by ``atexit`` and by ``logging.shutdown`` -- drains the
    queue with one bounded attempt per batch.
  - uvicorn access lines for ``/metrics`` and ``/health`` are not shipped
    (the promtail / Alloy probe-noise rule); the local file keeps them.

Stdlib only (``urllib.request``): the handler runs inside spawned chunk
children too, which inherit the env var and call ``configure()`` themselves.
"""

from __future__ import annotations

import atexit
import collections
import contextlib
import json
import logging
import os
import socket
import sys
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from collections.abc import Callable

#: Correlation ids shipped as structured metadata, never as stream labels.
METADATA_FIELDS: tuple[str, ...] = ("job_id", "doc_id", "doc_sha8", "run_id")

#: uvicorn access-log paths dropped before shipping (probe noise).
PROBE_PATH_PREFIXES: tuple[str, ...] = ("/metrics", "/health")
ACCESS_LOGGER = "uvicorn.access"

DEFAULT_BATCH_SIZE = 500
DEFAULT_FLUSH_INTERVAL_S = 2.0
DEFAULT_MAX_QUEUE = 10_000
DEFAULT_TIMEOUT_S = 5.0
DEFAULT_MAX_BACKOFF_S = 60.0
DEFAULT_SHUTDOWN_TIMEOUT_S = 3.0
REPORT_INTERVAL_S = 60.0

_Entry = tuple[tuple[tuple[str, str], ...], str, str, dict[str, str]]


def stream_labels(service: str, host: str | None = None) -> dict[str, str]:
    """The static labels: ``host`` (``DOCLING_BACKEND_NAME`` or hostname)
    and ``service``. ``level`` / ``kind`` are added per record."""
    name = host or os.environ.get("DOCLING_BACKEND_NAME", "").strip() or socket.gethostname()
    return {"host": name, "service": service}


def is_probe_access(record: logging.LogRecord) -> bool:
    """True for a uvicorn access line whose path is /metrics or /health.

    uvicorn logs ``(client, method, full_path, http_version, status)`` as the
    record args, so the path is read structurally, not by regex on the text.
    """
    if record.name != ACCESS_LOGGER:
        return False
    args = record.args
    if not isinstance(args, tuple) or len(args) < 3 or not isinstance(args[2], str):
        return False
    return args[2].startswith(PROBE_PATH_PREFIXES)


# -- HR2 erasure ---------------------------------------------------------------

#: Loki's compactor delete API (needs ``compactor.retention_enabled`` and a
#: ``delete_request_store``; infra's Loki 3.0 runs ``deletion_mode:
#: filter-and-delete``). Reachable in-cluster only, never through the gateway.
DELETE_PATH = "/loki/api/v1/delete"

#: Every stream that can carry a correlation id: promtail sets ``service`` on
#: every pod stream and ``stream_labels`` sets it on the Mac push. A delete
#: query needs a selector that is not empty-matching, so ``.+`` rather than ``.*``.
ERASURE_STREAM_SELECTOR = '{service=~".+"}'

#: A needle shorter than this is refused: an empty ``|= ""`` matches every line
#: in Loki, and a delete request cannot be undone once the compactor runs it.
MIN_ERASURE_NEEDLE_CHARS = 8

DEFAULT_DELETE_TIMEOUT_S = 10.0


def erasure_line_filters(
    doc_id: str, *, doc_sha8: str | None = None, doc_name_sha8: str | None = None
) -> list[str]:
    """The line-filter strings that identify one document's log lines.

    ``doc_id`` is matched bare: it is a uuid4, and it also appears in free-text
    messages (``"ERASE <doc_id> ..."``). The 8-hex digests are matched as the
    exact ``JsonFormatter`` key/value pair, so they cannot hit an unrelated hex
    run elsewhere in a line. ``doc_sha8`` is what the Mac docling-service lines
    carry (it never sees a ``doc_id``); ``doc_name_sha8`` covers the worker's
    pre-hash lines, which carry only the filename digest.
    """
    needles = [doc_id]
    if doc_sha8:
        needles.append(f'"doc_sha8": "{doc_sha8}"')
    if doc_name_sha8:
        needles.append(f'"doc_name_sha8": "{doc_name_sha8}"')
    return needles


def request_log_deletion(  # noqa: PLR0913
    base_url: str,
    needles: list[str],
    *,
    lookback_s: float,
    timeout: float = DEFAULT_DELETE_TIMEOUT_S,
    now: float | None = None,
    urlopen: Callable[..., object] | None = None,
) -> list[str]:
    """File one Loki delete request per needle over ``[now - lookback_s, now]``.

    Returns one error string per request Loki did not accept (an empty list
    means every request was accepted with 2xx). Never raises. The needle
    itself is never echoed into an error: it identifies the document.

    Loki applies an accepted request to queries straight away (filter) and
    removes the lines physically once ``delete_request_cancel_period`` has
    passed (delete); the store's own ``retention_period`` is the backstop.
    """
    opener = urlopen or urllib.request.urlopen
    end = int(now if now is not None else time.time())
    start = max(0, end - int(lookback_s))
    errors: list[str] = []
    for index, needle in enumerate(needles, start=1):
        tag = f"request {index}/{len(needles)}"
        if len(needle) < MIN_ERASURE_NEEDLE_CHARS:
            errors.append(f"{tag}: refused a line filter shorter than {MIN_ERASURE_NEEDLE_CHARS}")
            continue
        # json.dumps yields a double-quoted string with Go-compatible escapes,
        # which is what LogQL parses -- so a hostile doc_id cannot break out of
        # the line filter and widen the delete.
        query = f"{ERASURE_STREAM_SELECTOR} |= {json.dumps(needle)}"
        params = urllib.parse.urlencode({"query": query, "start": start, "end": end})
        request = urllib.request.Request(
            f"{base_url.rstrip('/')}{DELETE_PATH}?{params}", data=b"", method="POST"
        )
        try:
            with opener(request, timeout=timeout) as response:  # type: ignore[attr-defined]
                response.read()
        except urllib.error.HTTPError as exc:
            errors.append(f"{tag}: Loki answered HTTP {exc.code}")
        except Exception as exc:
            # ``reason`` only (URLError): str(exc) can quote the request URL,
            # and the URL carries the needle.
            reason = getattr(exc, "reason", None) or "request failed"
            errors.append(f"{tag}: {type(exc).__name__}: {reason}")
    return errors


class LokiPushHandler(logging.Handler):
    """Batching, non-blocking Loki push handler (see the module docstring)."""

    def __init__(  # noqa: PLR0913
        self,
        url: str,
        *,
        labels: dict[str, str],
        batch_size: int = DEFAULT_BATCH_SIZE,
        flush_interval: float = DEFAULT_FLUSH_INTERVAL_S,
        max_queue: int = DEFAULT_MAX_QUEUE,
        timeout: float = DEFAULT_TIMEOUT_S,
        max_backoff: float = DEFAULT_MAX_BACKOFF_S,
        shutdown_timeout: float = DEFAULT_SHUTDOWN_TIMEOUT_S,
        urlopen: Callable[..., object] | None = None,
    ) -> None:
        super().__init__()
        self.url = url
        self.labels = dict(labels)
        self.batch_size = max(1, batch_size)
        self.flush_interval = flush_interval
        self.max_queue = max(1, max_queue)
        self.timeout = timeout
        self.max_backoff = max_backoff
        self.shutdown_timeout = shutdown_timeout
        self.dropped = 0
        self.sent = 0
        self._urlopen = urlopen or urllib.request.urlopen
        self._queue: collections.deque[_Entry] = collections.deque()
        self._cond = threading.Condition()
        self._stopping = False
        self._closed = False
        self._local = threading.local()
        self._last_report = float("-inf")
        self._thread = threading.Thread(target=self._run, name="loki-push", daemon=True)
        self._thread.start()
        atexit.register(self.close)

    # -- producer side (any thread) -------------------------------------------

    def emit(self, record: logging.LogRecord) -> None:
        # The sender thread must never feed itself, and a record logged while
        # this handler is formatting (a library warning, say) must not recurse.
        if threading.current_thread() is self._thread or getattr(self._local, "busy", False):
            return
        self._local.busy = True
        try:
            if is_probe_access(record):
                return
            line = self.format(record)
            key = tuple(
                sorted(
                    {
                        **self.labels,
                        "level": str(record.levelname),
                        "kind": str(getattr(record, "kind", None) or "log"),
                    }.items()
                )
            )
            meta = {}
            for field in METADATA_FIELDS:
                value = getattr(record, field, None)
                if value not in (None, ""):
                    meta[field] = str(value)
            entry: _Entry = (key, str(int(record.created * 1_000_000_000)), line, meta)
            with self._cond:
                if self._closed:
                    return
                if len(self._queue) >= self.max_queue:
                    self._queue.popleft()
                    self.dropped += 1
                self._queue.append(entry)
                if len(self._queue) >= self.batch_size:
                    self._cond.notify()
        except Exception as exc:  # never into the app
            self._report(f"dropped a record it could not encode: {type(exc).__name__}")
        finally:
            self._local.busy = False

    def flush(self) -> None:
        """Non-blocking: wake the sender. ``close()`` is the draining call."""
        with self._cond:
            self._cond.notify()

    def close(self) -> None:
        with self._cond:
            already = self._closed
            self._closed = True
            self._stopping = True
            self._cond.notify_all()
        if not already:
            if threading.current_thread() is not self._thread:
                self._thread.join(self.shutdown_timeout)
            with contextlib.suppress(Exception):  # interpreter teardown
                atexit.unregister(self.close)
        super().close()

    # -- sender thread ---------------------------------------------------------

    def _take(self, limit: int) -> list[_Entry]:
        return [self._queue.popleft() for _ in range(min(limit, len(self._queue)))]

    def _run(self) -> None:
        batch: list[_Entry] = []
        backoff = 0.0
        while True:
            with self._cond:
                if not batch:
                    deadline = time.monotonic() + self.flush_interval
                    while not self._stopping and len(self._queue) < self.batch_size:
                        remaining = deadline - time.monotonic()
                        if remaining <= 0:
                            break
                        self._cond.wait(remaining)
                    if not self._stopping:
                        batch = self._take(self.batch_size)
                if self._stopping:
                    break
            if not batch:
                continue
            if self._send(batch):
                batch, backoff = [], 0.0
                continue
            backoff = min(max(1.0, backoff * 2), self.max_backoff)
            with self._cond:
                self._cond.wait_for(lambda: self._stopping, timeout=backoff)
        self._drain(batch)

    def _drain(self, held: list[_Entry]) -> None:
        """One attempt per batch, bounded by ``shutdown_timeout``."""
        deadline = time.monotonic() + self.shutdown_timeout
        with self._cond:
            pending = held + list(self._queue)
            self._queue.clear()
        for start in range(0, len(pending), self.batch_size):
            batch = pending[start : start + self.batch_size]
            if time.monotonic() >= deadline or not self._send(batch):
                with self._cond:
                    self.dropped += len(pending) - start
                return

    def _send(self, batch: list[_Entry]) -> bool:
        """POST one batch. True when done with it (sent, or rejected for good)."""
        streams: dict[tuple[tuple[str, str], ...], list[list[object]]] = {}
        for key, ts, line, meta in batch:
            streams.setdefault(key, []).append([ts, line, meta] if meta else [ts, line])
        try:
            body = json.dumps(
                {"streams": [{"stream": dict(k), "values": v} for k, v in streams.items()]},
                ensure_ascii=False,
            ).encode("utf-8")
            request = urllib.request.Request(
                self.url,
                data=body,
                headers={"Content-Type": "application/json"},
                method="POST",
            )
            with self._urlopen(request, timeout=self.timeout) as response:  # type: ignore[attr-defined]
                response.read()
        except urllib.error.HTTPError as exc:
            if 400 <= exc.code < 500 and exc.code != 429:
                with self._cond:
                    self.dropped += len(batch)
                self._report(f"Loki rejected a batch of {len(batch)} (HTTP {exc.code})")
                return True
            self._report(f"push failed (HTTP {exc.code}); retrying with backoff")
            return False
        except Exception as exc:
            self._report(f"push failed ({type(exc).__name__}: {exc}); retrying with backoff")
            return False
        with self._cond:
            self.sent += len(batch)
        return True

    def _report(self, message: str) -> None:
        """stderr, at most once per ``REPORT_INTERVAL_S`` -- never ``logging``."""
        now = time.monotonic()
        if now - self._last_report < REPORT_INTERVAL_S:
            return
        self._last_report = now
        try:
            stream = sys.__stderr__ or sys.stderr
            stream.write(
                f"pageindex obs: loki push to {self.url}: {message} "
                f"(dropped so far: {self.dropped})\n"
            )
            stream.flush()
        except Exception:  # pragma: no cover - stderr itself is gone
            pass
