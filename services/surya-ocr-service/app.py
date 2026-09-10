"""Surya OCR service — RFC-046 POC evaluation.

FastAPI wrapper around Surya v2 (vision-language OCR model, 650M params).
Surya auto-manages its own inference server (llama.cpp on Apple Silicon,
vllm on NVIDIA GPU) via SuryaInferenceManager.

Provides the same API contract as the PaddleOCR services so the eval
script can compare all engines side by side.

Endpoints:
  GET  /health           — liveness probe (checks model loaded)
  GET  /version          — model version info
  POST /ocr              — OCR a single image
  POST /ocr/pdf          — OCR all pages of a PDF
"""

import io
import logging
import os
import re
import time
from contextlib import asynccontextmanager

from fastapi import FastAPI, File, HTTPException, UploadFile
from PIL import Image
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

SURYA_DPI = int(os.environ.get("SURYA_DPI", "150"))

_TAG_RE = re.compile(r"<[^>]+>")


class RegionResult(BaseModel):
    text: str
    confidence: float
    bbox: list[list[float]] = Field(default_factory=list)


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
    version: str
    dpi: int


_manager = None
_recognition_predictor = None
_layout_predictor = None


def _ensure_models():
    global _manager, _recognition_predictor, _layout_predictor
    if _manager is not None:
        return

    from surya.inference import SuryaInferenceManager
    from surya.layout import LayoutPredictor
    from surya.recognition import RecognitionPredictor

    logger.info("Initializing Surya inference manager...")
    _manager = SuryaInferenceManager()
    _recognition_predictor = RecognitionPredictor(_manager)
    _layout_predictor = LayoutPredictor(_manager)
    logger.info("Surya models ready")


def _html_to_text(html: str) -> str:
    """Strip HTML tags to get plain text."""
    return _TAG_RE.sub("", html).strip()


def _ocr_image(pil_img: Image.Image) -> tuple[str, list[RegionResult], float]:
    """Run Surya OCR on a PIL image. Returns (full_text, regions, elapsed_s)."""
    _ensure_models()
    start = time.monotonic()

    layouts = _layout_predictor([pil_img])
    predictions = _recognition_predictor([pil_img], layouts)
    elapsed = time.monotonic() - start

    regions: list[RegionResult] = []
    text_parts: list[str] = []

    if predictions and len(predictions) > 0:
        page_pred = predictions[0]
        for block in page_pred.blocks:
            html = getattr(block, "html", "")
            block_text = _html_to_text(html)
            conf = getattr(block, "confidence", 0.0)
            bbox = []
            if hasattr(block, "bbox"):
                b = block.bbox
                bbox = [[b[0], b[1]], [b[2], b[1]], [b[2], b[3]], [b[0], b[3]]]
            if block_text:
                regions.append(RegionResult(text=block_text, confidence=conf, bbox=bbox))
                text_parts.append(block_text)

    full_text = "\n".join(text_parts)
    return full_text, regions, elapsed


def _page_to_pil(pdf_bytes: bytes, page_idx: int, dpi: int = 150) -> Image.Image:
    import fitz
    doc = fitz.open(stream=pdf_bytes, filetype="pdf")
    pix = doc[page_idx].get_pixmap(dpi=dpi)
    img = Image.frombytes("RGB", (pix.width, pix.height), pix.samples)
    doc.close()
    return img


@asynccontextmanager
async def lifespan(app: FastAPI):
    logger.info("Surya OCR service starting — DPI=%d", SURYA_DPI)
    try:
        _ensure_models()
    except Exception:
        logger.warning("Surya model init deferred to first request", exc_info=True)
    yield


app = FastAPI(title="Surya OCR Service", lifespan=lifespan)


@app.get("/health")
async def health():
    try:
        _ensure_models()
        return {"status": "ok"}
    except Exception as exc:
        return {"status": "error", "detail": str(exc)}


@app.get("/version", response_model=VersionResponse)
async def version():
    import surya
    ver = getattr(surya, "__version__", "unknown")
    return VersionResponse(
        model="surya-ocr-vlm-650M",
        backend="llama.cpp" if os.uname().sysname == "Darwin" else "vllm",
        version=ver,
        dpi=SURYA_DPI,
    )


@app.post("/ocr", response_model=OcrResponse)
async def ocr(file: UploadFile = File(...), lang: str | None = None):
    raw = await file.read()
    try:
        img = Image.open(io.BytesIO(raw)).convert("RGB")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid image: {exc}") from exc

    text, regions, elapsed = _ocr_image(img)
    confs = [r.confidence for r in regions]
    avg_conf = sum(confs) / len(confs) if confs else 0.0

    return OcrResponse(
        text=text,
        confidence=round(avg_conf, 4),
        lang=lang or "auto",
        regions=regions,
        region_count=len(regions),
        char_count=len(text),
        elapsed_s=round(elapsed, 3),
    )


@app.post("/ocr/pdf", response_model=PdfOcrResponse)
async def ocr_pdf(
    file: UploadFile = File(...),
    lang: str | None = None,
    max_pages: int = 50,
):
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
        pil_img = _page_to_pil(raw, idx, dpi=SURYA_DPI)
        text, regions, elapsed = _ocr_image(pil_img)
        confs = [r.confidence for r in regions]
        avg_conf = sum(confs) / len(confs) if confs else 0.0

        pages.append(
            OcrPageResult(
                page_index=idx,
                text=text,
                regions=regions,
                avg_confidence=round(avg_conf, 4),
                region_count=len(regions),
                char_count=len(text),
                elapsed_s=round(elapsed, 3),
                lang=lang or "auto",
            )
        )

    total_elapsed = time.monotonic() - total_start
    all_text = "\n\n---\n\n".join(p.text for p in pages)
    all_confs = [r.confidence for p in pages for r in p.regions]
    total_avg = sum(all_confs) / len(all_confs) if all_confs else 0.0

    return PdfOcrResponse(
        pages=pages,
        total_text=all_text,
        total_avg_confidence=round(total_avg, 4),
        total_char_count=sum(p.char_count for p in pages),
        total_elapsed_s=round(total_elapsed, 3),
        page_count=len(pages),
        lang=lang or "auto",
    )
