"""Docling EasyOCR spike service (RFC-036 D7).

Minimal FastAPI wrapper routing image bytes through Docling's EasyOCR-based
OCR pipeline, evaluated side-by-side with Tesseract and PaddleOCR on the
corpus's chart/table/scanned-Arabic images. Spike only -- not wired into
the main pipeline.
"""

import asyncio
import io
import logging
import os
import tempfile

from fastapi import FastAPI, File, HTTPException, UploadFile
from PIL import Image
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

OCR_LANG = os.environ.get("DOCLING_OCR_LANG", "ar,en")

# Request bounds. Both are refused before any decode or conversion work, so a
# hostile or accidental upload cannot exhaust the container ahead of OCR.
MAX_UPLOAD_BYTES = int(os.environ.get("DOCLING_OCR_MAX_UPLOAD_BYTES", 32 * 1024 * 1024))
MAX_IMAGE_PIXELS = int(os.environ.get("DOCLING_OCR_MAX_IMAGE_PIXELS", 64_000_000))

# Pillow's own decompression-bomb guard, aligned to the same bound.
Image.MAX_IMAGE_PIXELS = MAX_IMAGE_PIXELS

app = FastAPI(title="docling-ocr-service")

_converter = None


def _get_converter():
    global _converter
    if _converter is None:
        from docling.datamodel.base_models import InputFormat
        from docling.datamodel.pipeline_options import EasyOcrOptions, PdfPipelineOptions
        from docling.document_converter import DocumentConverter, PdfFormatOption

        ocr_options = EasyOcrOptions(lang=OCR_LANG.split(","), force_full_page_ocr=True)
        pipeline_options = PdfPipelineOptions(do_ocr=True, ocr_options=ocr_options)
        _converter = DocumentConverter(
            format_options={InputFormat.IMAGE: PdfFormatOption(pipeline_options=pipeline_options)}
        )
    return _converter


class OcrResponse(BaseModel):
    text: str
    confidence: float
    lang: str


@app.get("/health")
async def health():
    return {"status": "ok"}


def _run_ocr(raw: bytes) -> tuple[str, float]:
    """Decode, rasterise and OCR one image. Fully synchronous; callers must
    keep it off the event loop."""
    image = Image.open(io.BytesIO(raw))
    width, height = image.size
    if width * height > MAX_IMAGE_PIXELS:
        raise HTTPException(
            status_code=413,
            detail=(
                f"Image is {width}x{height} ({width * height} px); "
                f"limit is {MAX_IMAGE_PIXELS} px"
            ),
        )
    image = image.convert("RGB")

    with tempfile.NamedTemporaryFile(suffix=".png", delete=False) as tmp:
        image.save(tmp.name)
        tmp_path = tmp.name

    try:
        converter = _get_converter()
        result = converter.convert(tmp_path)
        text = result.document.export_to_markdown()
        confidences = [
            cell.confidence
            for page in getattr(result.document, "pages", {}).values()
            for cell in getattr(page, "cells", [])
            if getattr(cell, "confidence", None) is not None
        ]
    finally:
        os.unlink(tmp_path)
    confidence = sum(confidences) / len(confidences) if confidences else 0.0
    return text, confidence


@app.post("/ocr", response_model=OcrResponse)
async def ocr(file: UploadFile = File(...)):
    # Read bounded: an unrestricted upload could exhaust the container's
    # memory before OCR even begins.
    raw = await file.read(MAX_UPLOAD_BYTES + 1)
    if len(raw) > MAX_UPLOAD_BYTES:
        raise HTTPException(
            status_code=413,
            detail=f"Upload exceeds {MAX_UPLOAD_BYTES} bytes",
        )
    if not raw:
        raise HTTPException(status_code=400, detail="Empty upload")

    # Model initialisation and conversion are synchronous and take seconds;
    # running them inline froze /health and every concurrent request.
    text, confidence = await asyncio.to_thread(_run_ocr, raw)
    return OcrResponse(text=text, confidence=confidence, lang=OCR_LANG)
