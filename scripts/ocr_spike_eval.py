#!/usr/bin/env python3
"""RFC-036 D7 OCR engine evaluation spike.

Extracts test images from the corpus (chart images, scanned Arabic pages),
runs Tesseract, PaddleOCR, and Docling OCR on each, and computes accuracy
metrics to recommend whether either alternative engine beats the Tesseract
baseline by >= 20%. Spike only -- writes a comparison report, does not
touch production code.

Usage:
  python scripts/ocr_spike_eval.py                # default corpus targets
  python scripts/ocr_spike_eval.py --doc-store DIR
"""

import argparse
import json
import os
import re
import sys
import time
from pathlib import Path

import httpx

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "src"))
from pageindex_mcp.converters import _tesseract_ocr_image  # noqa: E402

PADDLEOCR_URL = os.environ.get("PADDLEOCR_SERVICE_URL", "http://localhost:8202/ocr")
DOCLING_OCR_URL = os.environ.get("DOCLING_OCR_SERVICE_URL", "http://localhost:8203/ocr")

# RFC-036 D7 affected documents (see "Affected Documents" in RFC).
DEFAULT_TARGETS = [
    "image pie chart about labor distribution in january 2025 - Copy.jpg",
    "قرار مجلس الوزراء رقم (106) لسنة 2022 بشأن اللائحة التنفيذية للمرسوم بقانون اتحادي رقم (9) لسنة 2022 بشأن عمال الخدمة المساعدة.pdf",
    "وارد رقم 597 من مكتب أبوظبي التنفيذي بشأن التعقيب على مرئيات حكومة أبوظبي حول برنامج مهارات المهن الحرفية - Copy.pdf",
]

_ARABIC_DIACRITICS_RE = re.compile(r"[ً-ٰٟ]")
_ARABIC_NUMERAL_RE = re.compile(r"[٠-٩]")
_ASCII_NUMERAL_RE = re.compile(r"[0-9]")


def extract_test_images(doc_store: Path, targets: list[str], out_dir: Path, max_pages: int = 3) -> list[Path]:
    """Extract PNG page renders (or pass through JPGs) for the given filenames."""
    out_dir.mkdir(parents=True, exist_ok=True)
    images: list[Path] = []
    for name in targets:
        src = doc_store / name
        if not src.exists():
            print(f"skip (not found): {name}", file=sys.stderr)
            continue
        if src.suffix.lower() in (".jpg", ".jpeg", ".png"):
            images.append(src)
            continue
        import fitz  # PyMuPDF, AGPL-3.0 -- spike-local, matches converters.py usage

        doc = fitz.open(str(src))
        for page_idx in range(min(max_pages, doc.page_count)):
            pix = doc[page_idx].get_pixmap(dpi=300)
            out_path = out_dir / f"{src.stem}_p{page_idx}.png"
            pix.save(str(out_path))
            images.append(out_path)
        doc.close()
    return images


def run_tesseract(image_path: Path) -> dict:
    langs = ["ara", "eng"] if any(ord(c) > 0x0590 for c in image_path.stem) else ["eng"]
    start = time.monotonic()
    text = _tesseract_ocr_image(str(image_path), langs)
    return {"engine": "tesseract", "text": text, "confidence": None, "elapsed_s": time.monotonic() - start}


def run_http_ocr(engine: str, url: str, image_path: Path) -> dict:
    start = time.monotonic()
    try:
        with open(image_path, "rb") as fh:
            resp = httpx.post(url, files={"file": (image_path.name, fh, "image/png")}, timeout=120)
        resp.raise_for_status()
        data = resp.json()
        return {
            "engine": engine,
            "text": data.get("text", ""),
            "confidence": data.get("confidence"),
            "elapsed_s": time.monotonic() - start,
        }
    except Exception as exc:  # service may not be running -- spike is best-effort
        return {"engine": engine, "text": "", "confidence": None, "elapsed_s": None, "error": str(exc)}


def structural_coherence(text: str) -> float:
    """Fraction of non-empty lines containing at least one digit or Arabic word char."""
    lines = [ln for ln in text.splitlines() if ln.strip()]
    if not lines:
        return 0.0
    coherent = sum(1 for ln in lines if _ASCII_NUMERAL_RE.search(ln) or _ARABIC_NUMERAL_RE.search(ln) or len(ln.strip()) >= 3)
    return coherent / len(lines)


def arabic_metrics(text: str) -> dict:
    return {
        "diacritics_count": len(_ARABIC_DIACRITICS_RE.findall(text)),
        "arabic_numeral_count": len(_ARABIC_NUMERAL_RE.findall(text)),
        "ascii_numeral_count": len(_ASCII_NUMERAL_RE.findall(text)),
    }


def char_accuracy(candidate: str, ground_truth: str | None) -> float | None:
    if not ground_truth:
        return None
    matches = sum(1 for a, b in zip(candidate, ground_truth) if a == b)
    return matches / max(len(ground_truth), 1)


def evaluate(image_path: Path, ground_truth: str | None = None) -> dict:
    results = [
        run_tesseract(image_path),
        run_http_ocr("paddleocr", PADDLEOCR_URL, image_path),
        run_http_ocr("docling_ocr", DOCLING_OCR_URL, image_path),
    ]
    for r in results:
        r["structural_coherence"] = structural_coherence(r["text"])
        r["char_accuracy"] = char_accuracy(r["text"], ground_truth)
        r.update({f"arabic_{k}": v for k, v in arabic_metrics(r["text"]).items()})
    return {"image": str(image_path), "results": results}


def recommend(all_results: list[dict]) -> dict:
    """Success criterion: an alt engine beats Tesseract's structural_coherence by >= 20%."""
    baseline = {}
    challengers: dict[str, list[float]] = {"paddleocr": [], "docling_ocr": []}
    for doc in all_results:
        by_engine = {r["engine"]: r for r in doc["results"]}
        baseline.setdefault("tesseract", []).append(by_engine["tesseract"]["structural_coherence"])
        for eng in challengers:
            if by_engine[eng].get("error") is None:
                challengers[eng].append(by_engine[eng]["structural_coherence"])

    baseline_avg = sum(baseline["tesseract"]) / len(baseline["tesseract"]) if baseline["tesseract"] else 0.0
    verdict = {"tesseract_baseline_avg_coherence": baseline_avg}
    winner = None
    for eng, scores in challengers.items():
        avg = sum(scores) / len(scores) if scores else None
        verdict[f"{eng}_avg_coherence"] = avg
        if avg is not None and baseline_avg > 0 and avg >= baseline_avg * 1.20:
            winner = eng
    verdict["recommendation"] = (
        f"{winner} clears the >=20% improvement bar over Tesseract" if winner
        else "neither PaddleOCR nor Docling OCR clears the >=20% improvement bar -- close spike, keep Tesseract"
    )
    return verdict


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--doc-store", default="doc_store")
    parser.add_argument("--out-dir", default=".agents/spikes/ocr_eval")
    parser.add_argument("--targets", nargs="*", default=DEFAULT_TARGETS)
    args = parser.parse_args()

    doc_store = Path(args.doc_store)
    out_dir = Path(args.out_dir)
    images = extract_test_images(doc_store, args.targets, out_dir / "images")

    all_results = [evaluate(img) for img in images]
    verdict = recommend(all_results)

    report = {"images_evaluated": len(images), "per_image": all_results, "verdict": verdict}
    report_path = out_dir / "ocr_spike_report.json"
    report_path.parent.mkdir(parents=True, exist_ok=True)
    report_path.write_text(json.dumps(report, indent=2, ensure_ascii=False))
    print(json.dumps(verdict, indent=2, ensure_ascii=False))
    print(f"full report: {report_path}")


if __name__ == "__main__":
    main()
