"""Vendor-neutral external Docling conversion service.

Exposes pdf_to_markdown_docling() and image_to_markdown() over HTTP so the
main PageIndex worker can offload the heavy (~1.9 GB RSS) Docling inference
to a separate process / container / serverless function.

The worker sends a presigned MinIO URL; this service downloads the file and
runs conversion locally, returning markdown + picture results as JSON.
"""

import asyncio
import base64
import contextlib
import hmac
import ipaddress
import logging
import multiprocessing
import os
import re
import socket
import tempfile
import threading
import time
import urllib.parse
from contextlib import asynccontextmanager
from dataclasses import dataclass, field
from typing import Annotated

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException, Request
from fastapi.responses import JSONResponse
from pydantic import BaseModel

from pageindex_mcp.converters.docling_conv import DoclingCancelled
from pageindex_mcp.obs.context import bind_log_context, current_context
from pageindex_mcp.obs.decisions import decision
from pageindex_mcp.obs.log_config import configure as configure_obs_logging

# RFC-052 R1 AC6: every line this service writes -- its own, docling's, and
# uvicorn's -- is one obs JSON envelope (pageindex_mcp.obs.formatter.JsonFormatter
# plus the ContextFilter that stamps job_id/doc_sha8/run_id from the context the
# middleware below binds). Loki parses one format for the cluster and the Mac.
UVICORN_LOGGERS = ("uvicorn", "uvicorn.error", "uvicorn.access")


def install_json_logging() -> None:
    """Route the root and uvicorn loggers through the obs JSON handler.

    uvicorn configures its loggers (own handlers, ``propagate=False``) before
    it imports this module, so they are stripped here and left to propagate
    to root. Idempotent; called at import and again in ``lifespan`` in case a
    launcher re-applied its own config in between. /health and /metrics
    access lines are kept -- as JSON, which promtail can drop by pattern (the
    in-process Loki push, ``obs/loki.py``, drops them itself).
    """
    configure_obs_logging()
    for name in UVICORN_LOGGERS:
        uv_logger = logging.getLogger(name)
        for handler in list(uv_logger.handlers):
            uv_logger.removeHandler(handler)
        uv_logger.propagate = True


install_json_logging()
logger = logging.getLogger(__name__)

BEARER_TOKEN = os.environ.get("DOCLING_SERVICE_BEARER_TOKEN", "")
# Anonymous access is an explicit opt-in for a local dev container only. The
# service is reachable over the internet (docling.saliltrehan.com), so an unset
# token must stop startup rather than silently disable auth.
ALLOW_ANONYMOUS = os.environ.get("DOCLING_SERVICE_ALLOW_ANONYMOUS", "") == "1"
DOWNLOAD_TIMEOUT_S = int(os.environ.get("DOWNLOAD_TIMEOUT_S", "120"))
# Refuse presigned URLs that resolve to a non-public address (loopback,
# private, link-local, Tailscale's 100.64/10). For hosts where no NetworkPolicy
# fences egress -- the native Mac copy behind docling.saliltrehan.com -- so a
# token holder cannot make it fetch the host's own services or its LAN. Off by
# default: the local compose copy downloads from MinIO at a private address.
# Checked at resolve time only (a rebinding DNS answer could still race it);
# httpx does not follow redirects, so a public URL cannot bounce inward.
BLOCK_PRIVATE_URLS = os.environ.get("DOCLING_BLOCK_PRIVATE_URLS", "") == "1"
# Conversions admitted at once. Every /convert/pdf request runs its actual
# Docling pass in a spawned child (pdf_to_markdown_docling's cancel_event
# branch always applies here -- this service always passes one -- delegates
# to _run_docling_chunk_with_timeout exactly like the chunked route), and
# that child peaks at ~2 GB RSS. The parent no longer preloads/caches a PDF
# converter of its own (repair cycle 2, finding 1): its baseline is uvicorn
# plus this module, not uvicorn plus a resident Docling model set, so the
# real per-slot cost is ~2 GB (child) rather than ~1-1.4 GB (parent cache) +
# ~2 GB (child) as it was when the lifespan warm-up cached a converter here
# too. The default of 1 makes the pod's memory limit hold however many
# workers call in parallel; extra requests queue here instead of OOM-killing
# the pod.
MAX_CONCURRENT = max(1, int(os.environ.get("DOCLING_MAX_CONCURRENT", "1")))
_convert_slots = asyncio.Semaphore(MAX_CONCURRENT)
# A slot holder whose client has gone is cancelled by the next request that
# finds every slot taken, once it has held its slot this long. Backstop for a
# disconnect the poller below missed (Tailscale / kube-proxy can hide one).
ORPHAN_GRACE_S = float(os.environ.get("DOCLING_ORPHAN_GRACE_S", "30"))
#: How often a /convert/pdf request checks its client and its X-Deadline.
CLIENT_POLL_S = 2.0

# Sized from this container's cgroup limits, not from env: the node type
# varies with what Hetzner has in stock, and the pod's limits follow the node
# (docling-node.sh up). The plan assumes the one-at-a-time admission above.
# A single-pass PDF runs in this process on every CPU; set
# before torch is first imported, which reads OMP_NUM_THREADS once.
from pageindex_mcp.converters.docling_resources import (  # noqa: E402
    available_cpus,
    available_memory_bytes,
    plan_docling,
)

CPUS = available_cpus()
os.environ["DOCLING_NUM_THREADS"] = str(CPUS)
os.environ["OMP_NUM_THREADS"] = str(CPUS)
logger.info(
    "docling sizing: %d CPUs, %d MiB memory (cgroup limits)",
    CPUS,
    available_memory_bytes() // (1024 * 1024),
)


def _pdf_page_count(path: str) -> int:
    import fitz  # PyMuPDF; already the chunked route's splitter

    with fitz.open(path) as doc:
        return doc.page_count


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def _verify_token(authorization: str | None = Header(None)) -> None:
    if not BEARER_TOKEN and ALLOW_ANONYMOUS:
        return
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    if not hmac.compare_digest(authorization[7:].encode(), BEARER_TOKEN.encode()):
        raise HTTPException(status_code=403, detail="Invalid bearer token")


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class PdfConvertRequest(BaseModel):
    presigned_url: str
    force_full_page_ocr: bool = False
    ocr_lang_override: list[str] | None = None
    pages_with_tables: list[int] | None = None
    # RFC-052 R2 AC6: run-length page classes, [[start, end, "T-t"], ...]
    # (0-based, inclusive). Absent (an older worker) means "no page classes":
    # every model stays on, exactly as before this field existed. Typed
    # loosely on purpose: a malformed value must degrade to "no page classes"
    # (WARNING) rather than 422 the whole conversion.
    page_classes: list | None = None


class ImageConvertRequest(BaseModel):
    presigned_url: str
    ocr_lang_override: list[str] | None = None


class PictureResultOut(BaseModel):
    ocr_text: str = ""
    png_bytes: str = ""
    page: int = 0
    bbox: dict | None = None
    description: str = ""
    skipped_reason: str = ""
    decorative: bool = False


class PdfConvertResponse(BaseModel):
    markdown: str
    picture_results: list[PictureResultOut]


class ImageConvertResponse(BaseModel):
    markdown: str


# ---------------------------------------------------------------------------
# Lifespan: warm the Docling model cache on startup (repair cycle 2, finding 1)
# ---------------------------------------------------------------------------

#: How long the startup warm-up subprocess gets before it is treated as
#: failed (model download over a slow link, or a wedged HF endpoint).
WARMUP_TIMEOUT_S = float(os.environ.get("DOCLING_WARMUP_TIMEOUT_S", "900"))

#: Flipped once the warm-up subprocess has returned (success or failure).
#: /health's "ready" field is False until this is True *and* the warm-up
#: itself reported success -- readiness is gated on the warm-up finishing
#: successfully, not merely on the app having started.
_warmup_done = False
#: True only when the warm-up subprocess loaded the Docling converter
#: without error. Kept separate from ``_warmup_done`` so a failed warm-up is
#: distinguishable from "still warming" in logs, even though both currently
#: read as "not ready" on /health.
_warmup_ok = False


def _warm_docling_models_child(result_queue: multiprocessing.Queue) -> None:
    """Load the Docling converter once, in a throwaway child process.

    Runs as the target of a spawned ``multiprocessing.Process``. Loading
    ``_docling_converter()`` here downloads/caches the model weights to disk
    (``DOCLING_ARTIFACTS_PATH`` or the HF cache) exactly as the old in-parent
    warm-up did, but the ~1-1.4 GiB those weights occupy is freed when this
    child exits instead of staying resident in the long-lived service
    process for its whole life.
    """
    try:
        from pageindex_mcp.converters import _docling_converter

        _docling_converter()
        result_queue.put(True)
    except Exception:
        logging.getLogger(__name__).warning(
            "docling model warm-up failed in child process", exc_info=True
        )
        with contextlib.suppress(Exception):
            result_queue.put(False)


def _warm_docling_models_subprocess(stop: threading.Event | None = None) -> bool:
    """Blocking: run ``_warm_docling_models_child`` and wait for its result.

    Called via ``asyncio.to_thread`` so it does not block the event loop.
    Best-effort throughout -- any failure to even start/read the child is
    treated as a failed warm-up, never raised. ``stop`` (set on shutdown)
    ends the wait within a second: cancelling the awaiting task does not
    stop a ``to_thread`` worker, and executor shutdown would otherwise wait
    out ``WARMUP_TIMEOUT_S``.
    """
    ctx = multiprocessing.get_context("spawn")
    result_queue: multiprocessing.Queue = ctx.Queue()
    proc = ctx.Process(target=_warm_docling_models_child, args=(result_queue,), daemon=True)
    proc.start()
    ok = False
    give_up = time.monotonic() + WARMUP_TIMEOUT_S
    while time.monotonic() < give_up and not (stop is not None and stop.is_set()):
        try:
            ok = bool(result_queue.get(timeout=1.0))
            break
        except Exception:
            if not proc.is_alive():
                # The child exited without (visibly) reporting: one last
                # read covers a result still in flight through the pipe.
                with contextlib.suppress(Exception):
                    ok = bool(result_queue.get(timeout=1.0))
                break
    proc.join(5)
    if proc.is_alive():
        with contextlib.suppress(Exception):
            proc.terminate()
        proc.join(5)
        if proc.is_alive():
            with contextlib.suppress(Exception):
                proc.kill()
            proc.join()
    return ok


async def _run_warmup(stop: threading.Event | None = None) -> None:
    """Background task started by ``lifespan``: warm models, flip readiness.

    Runs while the app already serves; /health answers 503 until it is done,
    so probes and the worker's readiness gate keep traffic away meanwhile
    (as before, when the lifespan blocked until warm).
    """
    global _warmup_done, _warmup_ok
    logger.info("Warming Docling model cache in a subprocess...")
    try:
        ok = await asyncio.to_thread(_warm_docling_models_subprocess, stop)
    except Exception:
        logger.warning("Docling model warm-up subprocess raised", exc_info=True)
        ok = False
    _warmup_ok = ok
    _warmup_done = True
    if ok:
        logger.info("Docling model cache warmed successfully")
    else:
        logger.warning("Docling model warm-up failed; the first /convert/pdf's child will be slow")


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not BEARER_TOKEN and not ALLOW_ANONYMOUS:
        raise RuntimeError(
            "DOCLING_SERVICE_BEARER_TOKEN is unset; refusing to start without auth "
            "(set DOCLING_SERVICE_ALLOW_ANONYMOUS=1 for a local dev container only)"
        )
    install_json_logging()
    # Finding 1 (repair cycle 2): every real PDF conversion here runs in its
    # own spawned child (pdf_to_markdown_docling's cancel_event branch, which
    # this service always takes), so a converter cached IN THIS PROCESS was
    # never actually used for a PDF -- it only doubled memory. Warm the model
    # cache in a short-lived subprocess instead (weights land on disk either
    # way) and track readiness via _warmup_done/_warmup_ok instead of holding
    # the converter here. /convert/image has no cancel_event / child-spawn
    # path, so it is the one endpoint that still uses the in-parent
    # ``_docling_converter()`` cache (see image_to_markdown/formats.py) --
    # left to load lazily on that endpoint's first call rather than
    # preloaded here, since preloading it would reintroduce the same
    # double-memory problem for the PDF path's benefit alone.
    warmup_stop = threading.Event()
    warmup_task = asyncio.create_task(_run_warmup(warmup_stop))
    try:
        yield
    finally:
        warmup_stop.set()
        warmup_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await warmup_task


# ---------------------------------------------------------------------------
# Correlation middleware (RFC-052 R1 AC6, D3)
# ---------------------------------------------------------------------------

#: Request header -> obs context field. ``shard`` is not an envelope field; it
#: is read back by the ``docling_chunk`` record (converters/docling_conv.py).
CORRELATION_HEADERS: dict[bytes, str] = {
    b"x-job-id": "job_id",
    b"x-doc-sha8": "doc_sha8",
    b"x-run-id": "run_id",
    b"x-shard": "shard",
}
# The middleware runs before auth, so a header is untrusted input headed for
# every log line of the request: bound its length and charset rather than
# let an unauthenticated caller write arbitrary text into Loki.
_CORRELATION_VALUE = re.compile(r"[A-Za-z0-9_.:/-]{1,128}")


def correlation_fields(headers) -> dict[str, str]:
    """Correlation fields from raw ASGI ``(name, value)`` header pairs.

    Missing, empty, ``"None"`` or malformed values are dropped -- binding
    them would make an unrelated request match a Grafana ``job_id`` query.
    """
    fields: dict[str, str] = {}
    for raw_name, raw_value in headers:
        field = CORRELATION_HEADERS.get(raw_name.lower())
        if field is None:
            continue
        value = raw_value.decode("latin-1").strip()
        if value.lower() == "none" or not _CORRELATION_VALUE.fullmatch(value):
            continue
        fields[field] = value
    return fields


class CorrelationMiddleware:
    """Pure ASGI middleware: binds the correlation headers for the request.

    Pure ASGI (not ``BaseHTTPMiddleware``) so the endpoint runs in this same
    task and sees the binding; ``asyncio.to_thread`` then copies it into the
    conversion thread, and ``_pdf_to_markdown_docling_chunked`` hands it on to
    each spawned chunk child explicitly. ``bind_log_context`` resets it in a
    ``finally``, so nothing leaks into the next request.
    """

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        if scope.get("type") != "http":
            await self.app(scope, receive, send)
            return
        fields = correlation_fields(scope.get("headers") or [])
        with bind_log_context(**fields):
            await self.app(scope, receive, send)


#: /convert/* requests currently inside the app -- downloading, queued on
#: ``_convert_slots`` or converting. Reported by /health so the Mac updater
#: (``macos/update.sh``) restarts the service only when it is 0. Touched only
#: on the event loop thread, so a plain int is safe.
_in_flight = 0


class InFlightMiddleware:
    """Pure ASGI middleware counting in-flight /convert/* requests."""

    def __init__(self, app):
        self.app = app

    async def __call__(self, scope, receive, send):
        global _in_flight

        if scope.get("type") != "http" or not str(scope.get("path", "")).startswith("/convert/"):
            await self.app(scope, receive, send)
            return
        _in_flight += 1
        try:
            await self.app(scope, receive, send)
        finally:
            _in_flight -= 1


app = FastAPI(title="Docling Conversion Service", lifespan=lifespan)
app.add_middleware(CorrelationMiddleware)
app.add_middleware(InFlightMiddleware)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _refuse_private_url(url: str) -> None:
    parts = urllib.parse.urlsplit(url)
    if parts.scheme not in ("http", "https") or not parts.hostname:
        raise HTTPException(status_code=400, detail="presigned_url must be an http(s) URL")
    port = parts.port or (443 if parts.scheme == "https" else 80)
    try:
        infos = socket.getaddrinfo(parts.hostname, port, proto=socket.IPPROTO_TCP)
    except socket.gaierror as exc:
        raise HTTPException(status_code=400, detail="presigned_url host does not resolve") from exc
    if any(not ipaddress.ip_address(info[4][0]).is_global for info in infos):
        raise HTTPException(
            status_code=400, detail="presigned_url resolves to a non-public address"
        )


async def _download_to_temp(url: str, suffix: str = ".pdf") -> str:
    """Download a file from a presigned URL to a temporary path."""
    if BLOCK_PRIVATE_URLS:
        await asyncio.to_thread(_refuse_private_url, url)
    tmp = tempfile.NamedTemporaryFile(suffix=suffix, delete=False)
    try:
        async with httpx.AsyncClient(timeout=DOWNLOAD_TIMEOUT_S) as client:
            async with client.stream("GET", url) as resp:
                resp.raise_for_status()
                async for chunk in resp.aiter_bytes(chunk_size=65536):
                    tmp.write(chunk)
        tmp.close()
        return tmp.name
    except Exception:
        tmp.close()
        os.unlink(tmp.name)
        raise


def _serialize_picture_result(pr: dict) -> dict:
    """Encode png_bytes as base64 for JSON transport."""
    out = dict(pr)
    raw = out.get("png_bytes")
    if raw and isinstance(raw, (bytes, bytearray)):
        out["png_bytes"] = base64.b64encode(raw).decode("ascii")
    elif raw is None:
        out["png_bytes"] = ""
    return out


# ---------------------------------------------------------------------------
# Cancellation (cold-start spec Q5 items 8-10)
# ---------------------------------------------------------------------------


@dataclass(eq=False)
class _Conversion:
    """One /convert/pdf request, from download until its response."""

    job_id: str | None
    request: Request
    deadline: float | None
    cancel_event: threading.Event = field(default_factory=threading.Event)
    #: Epoch seconds the conversion slot was acquired; None while queued.
    started_at: float | None = None
    cancel_reason: str | None = None
    #: Filled in by pipeline.pdf_to_markdown_docling's chunked route as
    #: {"total": <chunk count>, "done": <chunks completed>} (QA finding 5);
    #: empty for a direct-route conversion or one that never got far enough
    #: to know its chunk count.
    chunk_progress: dict = field(default_factory=dict)


#: Every /convert/pdf request past its download. Touched only on the event
#: loop thread (like ``_in_flight``), so a plain set is safe. A request sent
#: without X-Job-Id is here too, but POST /cancel cannot address it.
_conversions: set[_Conversion] = set()


def _cancel(conv: _Conversion, choice: str) -> None:
    """Set ``conv``'s cancel event once and log why.

    The chunked converter then starts no further chunk and terminates the
    running chunk processes; the conversion slot is released only when the
    conversion thread has actually returned.
    """
    if conv.cancel_event.is_set():
        return
    conv.cancel_reason = choice
    conv.cancel_event.set()
    held_s = round(time.time() - conv.started_at, 1) if conv.started_at is not None else 0.0
    with bind_log_context(job_id=conv.job_id):
        decision(
            event="docling_request_cancelled",
            choice=choice,
            reason=f"docling request cancelled: {choice}",
            attrs={
                "held_s": held_s,
                "chunks_done": conv.chunk_progress.get("done"),
                "chunks_total": conv.chunk_progress.get("total"),
            },
        )


async def _watch_client(conv: _Conversion) -> None:
    """Cancel ``conv`` once its client disconnects or its X-Deadline passes."""
    while not conv.cancel_event.is_set():
        if conv.deadline is not None and time.time() >= conv.deadline:
            _cancel(conv, "deadline")
            return
        if await conv.request.is_disconnected():
            _cancel(conv, "client_disconnect")
            return
        await asyncio.sleep(CLIENT_POLL_S)


async def _preempt_stale_orphans() -> None:
    """Pre-empt a slot holder whose ``X-Deadline`` has passed, or one past
    ``ORPHAN_GRACE_S`` whose client is gone.

    Runs once before a queued request starts waiting for a slot, and then
    every ``CLIENT_POLL_S`` while it keeps waiting (``_preempt_while_queued``)
    -- not just at the instant a new request happens to arrive, since the
    holder's deadline can pass, or its disconnect+grace backstop trip, at any
    point during a long queue wait. It cannot take a slot away; it makes the
    orphan let go of it sooner.
    """
    if not _convert_slots.locked():
        return
    now = time.time()
    for conv in list(_conversions):
        if conv.started_at is None or conv.cancel_event.is_set():
            continue
        if conv.deadline is not None and now >= conv.deadline:
            _cancel(conv, "deadline_preempted")
        elif now - conv.started_at >= ORPHAN_GRACE_S and await conv.request.is_disconnected():
            _cancel(conv, "orphan_preempted")


async def _preempt_while_queued() -> None:
    """Background loop: re-run ``_preempt_stale_orphans`` every
    ``CLIENT_POLL_S`` while this request waits on ``_convert_slots``.

    Cancelled once the slot is acquired (see ``convert_pdf``).
    """
    while True:
        await _preempt_stale_orphans()
        await asyncio.sleep(CLIENT_POLL_S)


async def _await_conversion(work, conv: _Conversion):
    """Await the conversion thread, keeping the slot until it has stopped.

    If this request task is itself cancelled, cancelling the awaiting task
    would not stop the thread (threads cannot be interrupted) and would free
    the slot under a still-running 2 GB conversion. Signal the thread instead
    and wait for it to unwind before re-raising.
    """
    fut = asyncio.ensure_future(work)
    try:
        return await asyncio.shield(fut)
    except asyncio.CancelledError:
        _cancel(conv, "client_disconnect")
        with contextlib.suppress(Exception):
            await fut
        raise


def _parse_deadline(raw: str | None) -> float | None:
    """``X-Deadline`` (epoch seconds) as a float; absent or malformed is None."""
    if raw is None:
        return None
    try:
        value = float(raw)
    except ValueError:
        value = float("nan")
    if value != value or value in (float("inf"), float("-inf")):
        logger.warning("ignoring malformed X-Deadline header")
        return None
    return value


# ---------------------------------------------------------------------------
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/health")
async def health():
    """Liveness plus what holds the conversion slot, for the reaper.

    ``current_job_id``/``started_at`` (epoch s) name the running conversion;
    with ``DOCLING_MAX_CONCURRENT`` > 1 they name the oldest one.

    Answers 503 until the startup warm-up subprocess has finished, because
    every consumer (k8s probes, ``mac_ok``/``cmd_up`` in the node controller,
    the worker's readiness gate) treats any 200 as ready -- before the warm-up
    moved to a subprocess, the lifespan blocked and /health only answered
    once warm. A *failed* warm-up still ends in 200: each conversion's child
    loads the models itself, so a flaky warm-up must not leave the service
    unready for good. ``ready`` says whether the warm-up succeeded.
    """
    running = [c for c in _conversions if c.started_at is not None]
    oldest = min(running, key=lambda c: c.started_at or 0.0, default=None)
    body = {
        "status": "ok" if _warmup_done else "warming",
        "in_flight": _in_flight,
        "current_job_id": oldest.job_id if oldest else None,
        "started_at": oldest.started_at if oldest else None,
        "ready": _warmup_done and _warmup_ok,
    }
    if not _warmup_done:
        return JSONResponse(status_code=503, content=body)
    return body


@app.post("/cancel/{job_id}", dependencies=[Depends(_verify_token)])
async def cancel_job(job_id: str):
    """Cancel the queued or running conversion sent with ``X-Job-Id: job_id``.

    For the worker's abort path: a disconnect is not always visible here.
    """
    matches = [c for c in _conversions if c.job_id == job_id]
    if not matches:
        raise HTTPException(status_code=404, detail="no in-flight conversion for that job id")
    for conv in matches:
        _cancel(conv, "cancel_endpoint")
    return {"cancelled": True, "job_id": job_id}


@app.get("/version")
async def version():
    from pageindex_mcp.config import CURRENT_PIPELINE_VERSION

    return {
        "commit_sha": os.environ.get("BUILD_SHA", "unknown"),
        "pipeline_version": CURRENT_PIPELINE_VERSION,
        "build_date": os.environ.get("BUILD_TIMESTAMP", "unknown"),
    }


def _request_page_classes(req: PdfConvertRequest):
    """The request's page classes as ``list[PageClass]``, or ``None``.

    ``None`` when the field is absent or malformed (malformed logs WARNING):
    both mean every model stays on (RFC-052 R2 AC7). P1 only validates and
    logs them; page-class chunking consumes them in P2.
    """
    if req.page_classes is None:
        return None
    from pageindex_mcp.converters.preclassify import page_classes_from_ranges

    try:
        classes = page_classes_from_ranges(req.page_classes)
    except (ValueError, TypeError):
        logger.warning(
            "ignoring malformed page_classes (%d ranges); every model stays on",
            len(req.page_classes),
            exc_info=True,
        )
        return None
    if classes is not None:
        logger.info(
            "page classes: %d pages in %d ranges, %d need tables, %d need OCR",
            len(classes),
            len(req.page_classes),
            sum(pc.needs_tables for pc in classes),
            sum(pc.needs_ocr for pc in classes),
        )
    return classes


@app.post("/convert/pdf", response_model=PdfConvertResponse, dependencies=[Depends(_verify_token)])
async def convert_pdf(
    req: PdfConvertRequest,
    request: Request,
    x_deadline: Annotated[str | None, Header()] = None,
):
    # QA finding 4: register (and start watching) the conversion BEFORE the
    # download, so POST /cancel/{job_id} and the deadline/disconnect watcher
    # both cover the download too -- previously a cancel sent during download
    # got a 404 (no in-flight conversion for that job id yet).
    conv = _Conversion(
        job_id=current_context().get("job_id"),  # type: ignore[arg-type]
        request=request,
        deadline=_parse_deadline(x_deadline),
    )
    _conversions.add(conv)
    watcher = asyncio.create_task(_watch_client(conv))
    tmp_path: str | None = None
    try:
        tmp_path = await _download_to_temp(req.presigned_url, suffix=".pdf")
        if conv.cancel_event.is_set():
            raise DoclingCancelled("cancelled during download")

        from pageindex_mcp.converters import pdf_to_markdown_docling

        plan = plan_docling(await asyncio.to_thread(_pdf_page_count, tmp_path))
        logger.info("docling plan: %s", plan)
        _request_page_classes(req)
        # QA finding 2: a single check right before queuing only catches an
        # orphan that is already stale at that instant. _preempt_while_queued
        # keeps re-checking (every CLIENT_POLL_S, including the holder's own
        # X-Deadline, not just disconnect) for as long as this request then
        # waits on the semaphore.
        preempt_task = asyncio.create_task(_preempt_while_queued())
        try:
            await _convert_slots.acquire()
        finally:
            preempt_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await preempt_task
        try:
            if conv.cancel_event.is_set():
                raise DoclingCancelled("cancelled while queued for a conversion slot")
            conv.started_at = time.time()
            _pages_set = set(req.pages_with_tables) if req.pages_with_tables is not None else None
            md, pic_results, _extraction_stages = await _await_conversion(
                asyncio.to_thread(
                    pdf_to_markdown_docling,
                    tmp_path,
                    force_full_page_ocr=req.force_full_page_ocr,
                    ocr_lang_override=req.ocr_lang_override,
                    max_pages=plan.pages_per_chunk,
                    workers=plan.workers,
                    num_threads=plan.threads_per_worker,
                    pages_with_tables=_pages_set,
                    cancel_event=conv.cancel_event,
                    progress=conv.chunk_progress,
                    # Finding 2 (repair cycle 2): the direct route's single
                    # "chunk" timeout should track this request's own
                    # X-Deadline, not a fixed per-chunk constant meant for
                    # the multi-chunk route. conv.deadline is None when the
                    # caller sent no X-Deadline; pdf_to_markdown_docling then
                    # runs that chunk unbounded (subject only to
                    # cancel_event), matching the old inline path's lack of
                    # any timeout of its own.
                    deadline=conv.deadline,
                ),
                conv,
            )
        finally:
            _convert_slots.release()
        serialized_pics = [_serialize_picture_result(pr) for pr in pic_results]  # type: ignore[arg-type]
        return PdfConvertResponse(
            markdown=md,
            picture_results=[PictureResultOut(**p) for p in serialized_pics],
        )
    except DoclingCancelled as exc:
        logger.info("PDF conversion cancelled (%s): %s", conv.cancel_reason, exc)
        # 499 (client closed request): usually nobody is left to read it.
        raise HTTPException(
            status_code=499, detail=f"conversion cancelled: {conv.cancel_reason}"
        ) from exc
    except Exception as exc:
        logger.exception("PDF conversion failed: %s", exc)
        raise HTTPException(status_code=500, detail="PDF conversion failed") from exc
    finally:
        watcher.cancel()
        _conversions.discard(conv)
        if tmp_path is not None:
            with contextlib.suppress(OSError):
                os.unlink(tmp_path)


@app.post(
    "/convert/image", response_model=ImageConvertResponse, dependencies=[Depends(_verify_token)]
)
async def convert_image(req: ImageConvertRequest):
    suffix = ".png"
    tmp_path = await _download_to_temp(req.presigned_url, suffix=suffix)
    try:
        from pageindex_mcp.converters import image_to_markdown

        async with _convert_slots:
            md = await asyncio.to_thread(
                image_to_markdown,
                tmp_path,
                ocr_lang_override=req.ocr_lang_override,
            )
        return ImageConvertResponse(markdown=md)
    except Exception as exc:
        logger.exception("Image conversion failed: %s", exc)
        raise HTTPException(status_code=500, detail="Image conversion failed") from exc
    finally:
        with contextlib.suppress(OSError):
            os.unlink(tmp_path)
