"""PaddleOCR service — RFC-046 POC evaluation.

FastAPI wrapper around PaddleOCR 3.x multilingual OCR engine.  Supports
image and PDF input, returns per-region bounding boxes + confidence scores
alongside full-page text.  Designed for side-by-side comparison with
Tesseract and Docling OCR across the full corpus (German, English, Arabic).

Endpoints:
  GET  /health           — liveness probe
  GET  /version          — engine + model version info
  POST /ocr              — OCR a single image, returns text + per-region details
  POST /ocr/pdf          — OCR all pages of a PDF, returns per-page results
  POST /ocr/batch        — OCR multiple images in one request (base64-encoded)
"""

import base64
import io
import logging
import os
import time
from concurrent.futures import ThreadPoolExecutor, as_completed
from contextlib import asynccontextmanager

os.environ.setdefault("PADDLE_PDX_DISABLE_MODEL_SOURCE_CHECK", "True")

import numpy as np
from fastapi import FastAPI, File, HTTPException, UploadFile
from PIL import Image
from pydantic import BaseModel, Field

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# --- Configuration via env vars ------------------------------------------------

_PADDLEOCR_USE_GPU_REQUESTED = os.environ.get("PADDLEOCR_USE_GPU", "false").lower() == "true"


def _gpu_available() -> bool:
    """True only when this image's paddlepaddle build can actually reach a GPU.

    The Dockerfile installs the CPU-only ``paddlepaddle`` wheel, so a Compose
    override of PADDLEOCR_USE_GPU=true otherwise asked every engine for a
    device that does not exist and failed at initialisation. The override is
    refused rather than honoured into a crash.
    """
    if not _PADDLEOCR_USE_GPU_REQUESTED:
        return False
    try:
        import paddle

        if paddle.device.is_compiled_with_cuda() and paddle.device.cuda.device_count() > 0:
            return True
    except Exception:
        logger.warning("PADDLEOCR_USE_GPU=true but paddle GPU probe failed", exc_info=True)
        return False
    logger.warning(
        "PADDLEOCR_USE_GPU=true ignored: this image ships the CPU-only "
        "paddlepaddle build, or no CUDA device is visible. Running on CPU."
    )
    return False


PADDLEOCR_USE_GPU = _gpu_available()
PADDLEOCR_VERSION = os.environ.get("PADDLEOCR_VERSION", "PP-OCRv6")
PADDLEOCR_ARABIC_VERSION = os.environ.get("PADDLEOCR_ARABIC_VERSION", "PP-OCRv5")
PADDLEOCR_DET_THRESH = float(os.environ.get("PADDLEOCR_DET_THRESH", "0.3"))
PADDLEOCR_DET_BOX_THRESH = float(os.environ.get("PADDLEOCR_DET_BOX_THRESH", "0.6"))
PADDLEOCR_REC_SCORE_THRESH = float(os.environ.get("PADDLEOCR_REC_SCORE_THRESH", "0.0"))
BEARER_TOKEN = os.environ.get("PADDLEOCR_BEARER_TOKEN", "")
OCR_PAGE_WORKERS = int(
    os.environ.get("OCR_PAGE_WORKERS", str(min(max(1, (os.cpu_count() or 4) // 4), 4)))
)
_page_pool: ThreadPoolExecutor | None = None

# Two engines: "en" covers English + German + all Latin scripts,
# "ar" is the dedicated Arabic recognition model.  The lang parameter
# on each request selects which one to use; any Latin-script language
# (de, fr, es, ...) is normalised to "en".
SUPPORTED_ENGINES = {"en", "ar"}
DEFAULT_LANG = "en"

# --- Engine cache --------------------------------------------------------------

_engines: dict[str, object] = {}


def _normalise_lang(lang: str) -> str:
    """Map any requested lang to one of the two supported engines."""
    if lang in ("ar", "arabic"):
        return "ar"
    return "en"


def _get_engine(lang: str = DEFAULT_LANG):
    canonical = _normalise_lang(lang)
    if canonical not in _engines:
        from paddleocr import PaddleOCR

        is_arabic = canonical == "ar"
        version = PADDLEOCR_ARABIC_VERSION if is_arabic else PADDLEOCR_VERSION
        logger.info(
            "Initialising PaddleOCR engine for lang=%s version=%s gpu=%s",
            canonical,
            version,
            PADDLEOCR_USE_GPU,
        )
        if is_arabic:
            _engines[canonical] = PaddleOCR(
                lang="ar",
                ocr_version=PADDLEOCR_ARABIC_VERSION,
                use_doc_orientation_classify=True,
                use_doc_unwarping=False,
                use_textline_orientation=True,
                text_det_thresh=PADDLEOCR_DET_THRESH,
                text_det_box_thresh=PADDLEOCR_DET_BOX_THRESH,
                text_rec_score_thresh=PADDLEOCR_REC_SCORE_THRESH,
                device="gpu" if PADDLEOCR_USE_GPU else "cpu",
            )
        else:
            _engines[canonical] = PaddleOCR(
                use_doc_orientation_classify=True,
                use_doc_unwarping=False,
                use_textline_orientation=True,
                text_det_thresh=PADDLEOCR_DET_THRESH,
                text_det_box_thresh=PADDLEOCR_DET_BOX_THRESH,
                text_rec_score_thresh=PADDLEOCR_REC_SCORE_THRESH,
                device="gpu" if PADDLEOCR_USE_GPU else "cpu",
            )
    return _engines[canonical]


# --- Pydantic models ----------------------------------------------------------


class RegionResult(BaseModel):
    text: str
    confidence: float
    bbox: list[list[float]] = Field(
        description="Polygon vertices [[x1,y1],[x2,y2],[x3,y3],[x4,y4]]"
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


class BatchItem(BaseModel):
    filename: str
    image_b64: str
    lang: str | None = None


class BatchRequest(BaseModel):
    items: list[BatchItem]


class BatchResponse(BaseModel):
    results: list[OcrResponse]


class VersionResponse(BaseModel):
    paddleocr_version: str
    ocr_model_version: str
    ocr_arabic_model_version: str
    default_lang: str
    gpu_enabled: bool


# --- Lifespan: warm default engine on startup ---------------------------------


@asynccontextmanager
async def lifespan(app: FastAPI):
    global _page_pool
    for lang in SUPPORTED_ENGINES:
        logger.info("Warming PaddleOCR engine (lang=%s)...", lang)
        try:
            _get_engine(lang)
            logger.info("PaddleOCR engine lang=%s warmed successfully", lang)
        except Exception:
            logger.warning(
                "Failed to warm PaddleOCR engine lang=%s; first request will be slow",
                lang,
                exc_info=True,
            )
    _page_pool = ThreadPoolExecutor(max_workers=OCR_PAGE_WORKERS, thread_name_prefix="ocr-page")
    logger.info("Page-parallel pool: %d workers (cpu_count=%s)", OCR_PAGE_WORKERS, os.cpu_count())
    yield
    _page_pool.shutdown(wait=False)
    _page_pool = None


app = FastAPI(title="PaddleOCR Service", lifespan=lifespan)


# --- Helpers -------------------------------------------------------------------


def _ocr_image(img_array: np.ndarray, lang: str) -> tuple[list[RegionResult], str, float]:
    """Run OCR on a numpy image array, return (regions, full_text, elapsed_s)."""
    engine = _get_engine(_normalise_lang(lang))
    start = time.monotonic()

    result = engine.predict(img_array)

    elapsed = time.monotonic() - start
    regions: list[RegionResult] = []
    lines: list[str] = []

    for page_result in result or []:
        if "rec_texts" in page_result:
            texts = page_result["rec_texts"]
            scores = page_result["rec_scores"]
            polys = page_result["dt_polys"]
            for i, txt in enumerate(texts):
                conf = scores[i] if i < len(scores) else 0.0
                bbox = polys[i].tolist() if i < len(polys) else []
                regions.append(RegionResult(text=txt, confidence=float(conf), bbox=bbox))
                lines.append(txt)

    return regions, "\n".join(lines), elapsed


def _ocr_one_page(doc_bytes: bytes, page_idx: int, lang: str, dpi: int = 300) -> OcrPageResult:
    import fitz

    doc = fitz.open(stream=doc_bytes, filetype="pdf")
    pix = doc[page_idx].get_pixmap(dpi=dpi)
    img_array = np.frombuffer(pix.samples, dtype=np.uint8).reshape(pix.h, pix.w, pix.n)
    if pix.n == 4:
        img_array = img_array[:, :, :3]
    doc.close()

    regions, text, elapsed = _ocr_image(img_array, lang)
    confidences = [r.confidence for r in regions]
    avg_conf = sum(confidences) / len(confidences) if confidences else 0.0
    return OcrPageResult(
        page_index=page_idx,
        text=text,
        regions=regions,
        avg_confidence=round(avg_conf, 4),
        region_count=len(regions),
        char_count=len(text),
        elapsed_s=round(elapsed, 3),
        lang=lang,
    )


def _image_from_upload(raw: bytes) -> np.ndarray:
    img = Image.open(io.BytesIO(raw)).convert("RGB")
    return np.array(img)


# --- Endpoints -----------------------------------------------------------------


@app.get("/health")
async def health():
    return {"status": "ok"}


@app.get("/version", response_model=VersionResponse)
async def version():
    try:
        import paddleocr

        ver = getattr(paddleocr, "__version__", "unknown")
    except ImportError:
        ver = "not installed"
    return VersionResponse(
        paddleocr_version=ver,
        ocr_model_version=PADDLEOCR_VERSION,
        ocr_arabic_model_version=PADDLEOCR_ARABIC_VERSION,
        default_lang=DEFAULT_LANG,
        gpu_enabled=PADDLEOCR_USE_GPU,
    )


@app.post("/ocr", response_model=OcrResponse)
async def ocr(file: UploadFile = File(...), lang: str | None = None):
    """OCR a single image. Accepts PNG/JPG/TIFF."""
    effective_lang = lang or DEFAULT_LANG
    raw = await file.read()
    try:
        img_array = _image_from_upload(raw)
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid image: {exc}") from exc

    regions, text, elapsed = _ocr_image(img_array, effective_lang)
    confidences = [r.confidence for r in regions]
    avg_conf = sum(confidences) / len(confidences) if confidences else 0.0

    return OcrResponse(
        text=text,
        confidence=avg_conf,
        lang=effective_lang,
        regions=regions,
        region_count=len(regions),
        char_count=len(text),
        elapsed_s=round(elapsed, 3),
    )


@app.post("/ocr/pdf", response_model=PdfOcrResponse)
async def ocr_pdf(file: UploadFile = File(...), lang: str | None = None, max_pages: int = 50):
    """OCR all pages of a PDF. Renders each page at 300 DPI then runs OCR."""
    effective_lang = lang or DEFAULT_LANG
    raw = await file.read()

    try:
        import fitz
    except ImportError:
        raise HTTPException(
            status_code=500, detail="PyMuPDF (fitz) not available for PDF rendering"
        )

    try:
        doc = fitz.open(stream=raw, filetype="pdf")
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"Invalid PDF: {exc}") from exc

    total_start = time.monotonic()
    page_limit = min(max_pages, doc.page_count)
    doc.close()

    if _page_pool and page_limit > 1:
        futures = {
            _page_pool.submit(_ocr_one_page, raw, idx, effective_lang): idx
            for idx in range(page_limit)
        }
        page_map: dict[int, OcrPageResult] = {}
        for fut in as_completed(futures):
            idx = futures[fut]
            page_map[idx] = fut.result()
        pages = [page_map[i] for i in range(page_limit)]
    else:
        pages = [_ocr_one_page(raw, idx, effective_lang) for idx in range(page_limit)]

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
        lang=effective_lang,
    )


@app.post("/ocr/batch", response_model=BatchResponse)
async def ocr_batch(req: BatchRequest):
    """OCR multiple base64-encoded images in one request."""
    results: list[OcrResponse] = []
    for item in req.items:
        effective_lang = item.lang or DEFAULT_LANG
        try:
            raw = base64.b64decode(item.image_b64)
            img_array = _image_from_upload(raw)
        except Exception as exc:
            results.append(
                OcrResponse(
                    text="",
                    confidence=0.0,
                    lang=effective_lang,
                    regions=[],
                    region_count=0,
                    char_count=0,
                    elapsed_s=0.0,
                )
            )
            logger.warning("Batch item %s failed: %s", item.filename, exc)
            continue

        regions, text, elapsed = _ocr_image(img_array, effective_lang)
        confidences = [r.confidence for r in regions]
        avg_conf = sum(confidences) / len(confidences) if confidences else 0.0
        results.append(
            OcrResponse(
                text=text,
                confidence=avg_conf,
                lang=effective_lang,
                regions=regions,
                region_count=len(regions),
                char_count=len(text),
                elapsed_s=round(elapsed, 3),
            )
        )

    return BatchResponse(results=results)
