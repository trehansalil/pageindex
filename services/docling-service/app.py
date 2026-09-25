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
import logging
import os
import tempfile
from contextlib import asynccontextmanager

import httpx
from fastapi import Depends, FastAPI, Header, HTTPException
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BEARER_TOKEN = os.environ.get("DOCLING_SERVICE_BEARER_TOKEN", "")
# Anonymous access is an explicit opt-in for a local dev container only. The
# service is reachable over the internet (docling.saliltrehan.com), so an unset
# token must stop startup rather than silently disable auth.
ALLOW_ANONYMOUS = os.environ.get("DOCLING_SERVICE_ALLOW_ANONYMOUS", "") == "1"
DOWNLOAD_TIMEOUT_S = int(os.environ.get("DOWNLOAD_TIMEOUT_S", "120"))
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
    logger.info("Warming Docling converter cache...")
    try:
        from pageindex_mcp.converters import _docling_converter

        _docling_converter()
        logger.info("Docling converter cache warmed successfully")
    except Exception:
        logger.warning("Failed to warm converter cache; first request will be slow", exc_info=True)
    yield


app = FastAPI(title="Docling Conversion Service", lifespan=lifespan)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


async def _download_to_temp(url: str, suffix: str = ".pdf") -> str:
    """Download a file from a presigned URL to a temporary path."""
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
