"""Vendor-neutral external Docling conversion service.

Exposes pdf_to_markdown_docling() and image_to_markdown() over HTTP so the
main PageIndex worker can offload the heavy (~1.9 GB RSS) Docling inference
to a separate process / container / serverless function.

The worker sends a presigned MinIO URL; this service downloads the file and
runs conversion locally, returning markdown + picture results as JSON.
"""

import base64
import contextlib
import logging
import os
import tempfile
from contextlib import asynccontextmanager

import httpx
from fastapi import Depends, FastAPI, HTTPException, Header
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

BEARER_TOKEN = os.environ.get("DOCLING_SERVICE_BEARER_TOKEN", "")
DOWNLOAD_TIMEOUT_S = int(os.environ.get("DOWNLOAD_TIMEOUT_S", "120"))


# ---------------------------------------------------------------------------
# Auth
# ---------------------------------------------------------------------------


def _verify_token(authorization: str | None = Header(None)) -> None:
    if not BEARER_TOKEN:
        return
    if not authorization or not authorization.startswith("Bearer "):
        raise HTTPException(status_code=401, detail="Missing bearer token")
    if authorization[7:] != BEARER_TOKEN:
        raise HTTPException(status_code=403, detail="Invalid bearer token")


# ---------------------------------------------------------------------------
# Request / response models
# ---------------------------------------------------------------------------


class PdfConvertRequest(BaseModel):
    presigned_url: str
    force_full_page_ocr: bool = False
    ocr_lang_override: list[str] | None = None


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


@app.post("/convert/pdf", response_model=PdfConvertResponse, dependencies=[Depends(_verify_token)])
async def convert_pdf(req: PdfConvertRequest):
    import asyncio

    tmp_path = await _download_to_temp(req.presigned_url, suffix=".pdf")
    try:
        from pageindex_mcp.converters import pdf_to_markdown_docling

        md, pic_results = await asyncio.to_thread(
            pdf_to_markdown_docling,
            tmp_path,
            force_full_page_ocr=req.force_full_page_ocr,
            ocr_lang_override=req.ocr_lang_override,
        )
        serialized_pics = [_serialize_picture_result(pr) for pr in pic_results]
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
    import asyncio

    suffix = ".png"
    tmp_path = await _download_to_temp(req.presigned_url, suffix=suffix)
    try:
        from pageindex_mcp.converters import image_to_markdown

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
