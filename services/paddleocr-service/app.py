"""PaddleOCR spike service (RFC-036 D7).

Minimal FastAPI wrapper around PaddleOCR's multilingual model, evaluated
side-by-side with Tesseract and Docling OCR on the corpus's chart/table/
scanned-Arabic images. Spike only -- not wired into the main pipeline.
"""

import io
import logging
import os

import numpy as np
from fastapi import FastAPI, File, UploadFile
from PIL import Image
from pydantic import BaseModel

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

OCR_LANG = os.environ.get("PADDLEOCR_LANG", "ar")
USE_GPU = os.environ.get("PADDLEOCR_USE_GPU", "false").lower() == "true"

app = FastAPI(title="paddleocr-service")

_ocr_engine = None


def _get_engine():
    global _ocr_engine
    if _ocr_engine is None:
        from paddleocr import PaddleOCR

        _ocr_engine = PaddleOCR(use_angle_cls=True, lang=OCR_LANG, use_gpu=USE_GPU, show_log=False)
    return _ocr_engine


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
    engine = _get_engine()
    result = engine.ocr(np.array(image), cls=True)

    lines: list[str] = []
    confidences: list[float] = []
    for page in result or []:
        for _box, (line_text, line_conf) in page or []:
            lines.append(line_text)
            confidences.append(line_conf)

    text = "\n".join(lines)
    confidence = sum(confidences) / len(confidences) if confidences else 0.0
    return OcrResponse(text=text, confidence=confidence, lang=OCR_LANG)
