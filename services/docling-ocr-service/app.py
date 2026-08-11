"""Docling EasyOCR spike service (RFC-036 D7).

Minimal FastAPI wrapper routing image bytes through Docling's EasyOCR-based
OCR pipeline, evaluated side-by-side with Tesseract and PaddleOCR on the
corpus's chart/table/scanned-Arabic images. Spike only -- not wired into
the main pipeline.
"""

import io
import logging
import os
import tempfile

from fastapi import FastAPI, File, UploadFile
from PIL import Image
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

OCR_LANG = os.environ.get("DOCLING_OCR_LANG", "ar,en")

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


@app.post("/ocr", response_model=OcrResponse)
async def ocr(file: UploadFile = File(...)):
    raw = await file.read()
    image = Image.open(io.BytesIO(raw)).convert("RGB")

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
    return OcrResponse(text=text, confidence=confidence, lang=OCR_LANG)
