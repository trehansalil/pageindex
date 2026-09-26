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
import os
import re
import socket
import tempfile
import urllib.parse
from contextlib import asynccontextmanager

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel

from pageindex_mcp.obs.context import bind_log_context
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
    access lines are kept -- as JSON, which promtail can drop by pattern.
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
# Conversions admitted at once. Each peaks at ~2 GB RSS, so the default of 1
# makes the pod's memory limit hold however many workers call in parallel;
# extra requests queue here instead of OOM-killing the pod.
MAX_CONCURRENT = max(1, int(os.environ.get("DOCLING_MAX_CONCURRENT", "1")))
_convert_slots = asyncio.Semaphore(MAX_CONCURRENT)

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
# Lifespan: warm the Docling converter cache on startup
# ---------------------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    if not BEARER_TOKEN and not ALLOW_ANONYMOUS:
        raise RuntimeError(
            "DOCLING_SERVICE_BEARER_TOKEN is unset; refusing to start without auth "
            "(set DOCLING_SERVICE_ALLOW_ANONYMOUS=1 for a local dev container only)"
        )
    install_json_logging()
    logger.info("Warming Docling converter cache...")
    try:
        from pageindex_mcp.converters import _docling_converter

        _docling_converter()
        logger.info("Docling converter cache warmed successfully")
    except Exception:
        logger.warning("Failed to warm converter cache; first request will be slow", exc_info=True)
    yield


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


app = FastAPI(title="Docling Conversion Service", lifespan=lifespan)
app.add_middleware(CorrelationMiddleware)


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
# Endpoints
# ---------------------------------------------------------------------------


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/version")
async def version():
    from pageindex_mcp.config import CURRENT_PIPELINE_VERSION

    return {
        "commit_sha": os.environ.get("BUILD_SHA", "unknown"),
        "pipeline_version": CURRENT_PIPELINE_VERSION,
        "build_date": os.environ.get("BUILD_TIMESTAMP", "unknown"),
    }


@app.post("/convert/pdf", response_model=PdfConvertResponse, dependencies=[Depends(_verify_token)])
async def convert_pdf(req: PdfConvertRequest):
    tmp_path = await _download_to_temp(req.presigned_url, suffix=".pdf")
    try:
        from pageindex_mcp.converters import pdf_to_markdown_docling

        plan = plan_docling(await asyncio.to_thread(_pdf_page_count, tmp_path))
        logger.info("docling plan: %s", plan)
        async with _convert_slots:
            _pages_set = set(req.pages_with_tables) if req.pages_with_tables is not None else None
            md, pic_results, _extraction_stages = await asyncio.to_thread(
                pdf_to_markdown_docling,
                tmp_path,
                force_full_page_ocr=req.force_full_page_ocr,
                ocr_lang_override=req.ocr_lang_override,
                max_pages=plan.pages_per_chunk,
                workers=plan.workers,
                num_threads=plan.threads_per_worker,
                pages_with_tables=_pages_set,
            )
        serialized_pics = [_serialize_picture_result(pr) for pr in pic_results]  # type: ignore[arg-type]
        return PdfConvertResponse(
            markdown=md,
            picture_results=[PictureResultOut(**p) for p in serialized_pics],
        )
    except Exception as exc:
        logger.exception("PDF conversion failed: %s", exc)
        raise HTTPException(status_code=500, detail="PDF conversion failed") from exc
    finally:
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
