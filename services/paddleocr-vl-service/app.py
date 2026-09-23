"""PaddleOCR-VL 1.6 service — RFC-046 POC evaluation.

FastAPI wrapper around PaddleOCR-VL 1.6 (Vision-Language OCR model)
served via Ollama.  Uses the OpenAI-compatible chat API with image
input and the "OCR:" prompt to extract text from document pages.

This is a *separate* service from the PP-OCRv5 service (port 8202).
It provides the same API contract so the eval script can compare
all three engines side by side.

Endpoints:
  GET  /health           — liveness probe (checks Ollama model loaded)
  GET  /version          — model version info
  POST /ocr              — OCR a single image
  POST /ocr/pdf          — OCR all pages of a PDF
"""

import base64
import io
import logging
import os
import time
from contextlib import asynccontextmanager

import numpy as np
import requests as http_requests
from fastapi import FastAPI, File, HTTPException, UploadFile
from PIL import Image
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Configuration -----------------------------------------------------------

OLLAMA_BASE_URL = os.environ.get("OLLAMA_BASE_URL", "http://localhost:11434")
OLLAMA_MODEL = os.environ.get("PADDLEOCR_VL_MODEL", "hf.co/PaddlePaddle/PaddleOCR-VL-1.6-GGUF")
VL_TIMEOUT = int(os.environ.get("VL_TIMEOUT", "300"))
VL_DPI = int(os.environ.get("VL_DPI", "150"))

# --- Pydantic models (same contract as PP-OCRv5 service) ---------------------


class RegionResult(BaseModel):
    text: str
    confidence: float
    bbox: list[list[float]] = Field(
        default_factory=list,
        description="Not available for VL model — always empty",
    )


class OcrPageResult(BaseModel):
    page_index: int = 0
    text: str
    regions: list[RegionResult]
    avg_confidence: float
    region_count: int
    char_count: int
    elapsed_s: float
    lang: str


class OcrResponse(BaseModel):
    text: str
    confidence: float
    lang: str
    regions: list[RegionResult] = []
    region_count: int = 0
    char_count: int = 0
    elapsed_s: float = 0.0


class PdfOcrResponse(BaseModel):
    pages: list[OcrPageResult]
    total_text: str
    total_avg_confidence: float
    total_char_count: int
    total_elapsed_s: float
    page_count: int
    lang: str


class VersionResponse(BaseModel):
    model: str
    backend: str
    ollama_base_url: str
    dpi: int


# --- Ollama VL inference -----------------------------------------------------


def _ocr_image_bytes(img_bytes: bytes) -> tuple[str, float]:
    """Send image to Ollama VL model, return (text, elapsed_s)."""
    b64 = base64.b64encode(img_bytes).decode()
    start = time.monotonic()

    resp = http_requests.post(
        f"{OLLAMA_BASE_URL}/api/chat",
        json={
            "model": OLLAMA_MODEL,
            "messages": [{"role": "user", "content": "OCR:", "images": [b64]}],
            "stream": False,
            "options": {"temperature": 0},
        },
        timeout=VL_TIMEOUT,
    )
    elapsed = time.monotonic() - start

    if resp.status_code != 200:
        raise HTTPException(
            status_code=502,
            detail=f"Ollama returned {resp.status_code}: {resp.text[:200]}",
        )

    data = resp.json()
    text = data.get("message", {}).get("content", "")
    return text, elapsed


def _page_to_png(pdf_bytes: bytes, page_idx: int, dpi: int = 150) -> bytes:
    import fitz

    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    pix = doc[page_idx].get_pixmap(dpi=dpi)
    png = pix.tobytes("png")
    doc.close()
    return png


# --- Lifespan ----------------------------------------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info(
        "PaddleOCR-VL service starting — model=%s ollama=%s",
        OLLAMA_MODEL,
        OLLAMA_BASE_URL,
    )
    try:
        resp = http_requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=10)
        models = [m["name"] for m in resp.json().get("models", [])]
        if not any(OLLAMA_MODEL in m for m in models):
            logger.warning(
                "Model %s not found in Ollama. Available: %s",
                OLLAMA_MODEL,
                models,
            )
        else:
            logger.info("Model %s confirmed available in Ollama", OLLAMA_MODEL)
    except Exception:
        logger.warning("Could not reach Ollama at %s", OLLAMA_BASE_URL, exc_info=True)
    yield


app = FastAPI(title="PaddleOCR-VL Service", lifespan=lifespan)


# --- Endpoints ---------------------------------------------------------------


@app.get("/health")
async def health():
    try:
        resp = http_requests.get(f"{OLLAMA_BASE_URL}/api/tags", timeout=5)
        models = [m["name"] for m in resp.json().get("models", [])]
        model_loaded = any(OLLAMA_MODEL in m for m in models)
    except Exception:
        return {"status": "error", "detail": "Cannot reach Ollama"}
    if not model_loaded:
        return {"status": "error", "detail": f"Model {OLLAMA_MODEL} not loaded"}
    return {"status": "ok"}


@app.get("/version", response_model=VersionResponse)
async def version():
    return VersionResponse(
        model=OLLAMA_MODEL,
        backend="ollama",
        ollama_base_url=OLLAMA_BASE_URL,
        dpi=VL_DPI,
    )


@app.post("/ocr", response_model=OcrResponse)
async def ocr(file: UploadFile = File(...), lang: str | None = None):
    """OCR a single image via VL model. lang is accepted but ignored — VL is multilingual."""
    raw = await file.read()
    try:
        img = Image.open(io.BytesIO(raw)).convert("RGB")
        buf = io.BytesIO()
        img.save(buf, format="PNG")
        png_bytes = buf.getvalue()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid image: {exc}") from exc

    text, elapsed = _ocr_image_bytes(png_bytes)
    region = RegionResult(text=text, confidence=1.0, bbox=[])

    return OcrResponse(
        text=text,
        confidence=1.0,
        lang=lang or "auto",
        regions=[region] if text else [],
        region_count=1 if text else 0,
        char_count=len(text),
        elapsed_s=round(elapsed, 3),
    )


@app.post("/ocr/pdf", response_model=PdfOcrResponse)
async def ocr_pdf(
    file: UploadFile = File(...),
    lang: str | None = None,
    max_pages: int = 50,
):
    """OCR all pages of a PDF via VL model."""
    raw = await file.read()

    try:
        import fitz
    except ImportError:
        raise HTTPException(
            status_code=500,
            detail="PyMuPDF (fitz) not available for PDF rendering",
        )

    try:
        doc = fitz.open(stream=raw, filetype="pdf")
        page_count = doc.page_count
        doc.close()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid PDF: {exc}") from exc

    total_start = time.monotonic()
    page_limit = min(max_pages, page_count)
    pages: list[OcrPageResult] = []

    for idx in range(page_limit):
        png_bytes = _page_to_png(raw, idx, dpi=VL_DPI)
        text, elapsed = _ocr_image_bytes(png_bytes)
        region = RegionResult(text=text, confidence=1.0, bbox=[])
        pages.append(
            OcrPageResult(
                page_index=idx,
                text=text,
                regions=[region] if text else [],
                avg_confidence=1.0,
                region_count=1 if text else 0,
                char_count=len(text),
                elapsed_s=round(elapsed, 3),
                lang=lang or "auto",
            )
        )

    total_elapsed = time.monotonic() - total_start
    all_text = "\n\n---\n\n".join(p.text for p in pages)

    return PdfOcrResponse(
        pages=pages,
        total_text=all_text,
        total_avg_confidence=1.0,
        total_char_count=sum(p.char_count for p in pages),
        total_elapsed_s=round(total_elapsed, 3),
        page_count=len(pages),
        lang=lang or "auto",
    )
