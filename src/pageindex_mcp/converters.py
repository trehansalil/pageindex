"""Document format conversion helpers and tree search utilities."""

import asyncio
import logging
import os
import re
import shutil
import subprocess
import tempfile
from typing import Callable, cast


logger = logging.getLogger(__name__)

_DASH_TRANSLATION = {0x2013: "-", 0x2014: "-", 0x2212: "-"}


def normalize_dashes(s: str) -> str:
    """Replace en-dash (U+2013), em-dash (U+2014) and minus (U+2212) with ASCII '-' (CONV-01-C2)."""
    return s.translate(_DASH_TRANSLATION)


_HEADING_RE = re.compile(r"^(#{1,6})(?=\s)", re.MULTILINE)


def _relevel_headings(md: str) -> str:
    """Promote markdown headings so the shallowest present level becomes H1 (#)."""
    levels = [len(m.group(1)) for m in _HEADING_RE.finditer(md)]
    if not levels:
        return md
    shift = min(levels) - 1
    if shift <= 0:
        return md
    return _HEADING_RE.sub(lambda m: "#" * (len(m.group(1)) - shift), md)


def pdf_to_markdown(pdf_path: str) -> str:
    """Primary PDF route (INDEX-01-C1): pymupdf4llm -> relevel headings -> normalize dashes.
    Raises on empty/failed extraction so the caller can fall back to page_index (INDEX-01-C2)."""
    import pymupdf4llm
    # to_markdown() returns a str with default args; it only returns list[dict]
    # when page_chunks=True (which we do not pass). Cast to str for the type checker.
    md = cast(str, pymupdf4llm.to_markdown(pdf_path))
    if not md or not md.strip():
        raise RuntimeError(f"pdf_to_markdown produced empty output for {pdf_path}")
    return normalize_dashes(_relevel_headings(md))


def pdf_to_markdown_docling(pdf_path: str) -> str:
    """MIT-licensed layout-aware PDF route (RFC-003 D3 / HR4 AGPL escape).

    Docling's Heron RT-DETRv2 layout model + TableFormer -> markdown -> relevel
    headings -> normalize dashes. Validated head-to-head against pymupdf4llm on
    the German insurance corpus (2026-05-31): Docling resolves the ``fl``-ligature
    corruption pymupdf4llm leaves in legal terms (e.g. ``Haftpflicht`` rendered as
    ``Haftpficht``), at ~2.5-6x the CPU runtime.

    The accelerator is pinned to CPU unconditionally — no MPS, no CUDA. This is a
    deliberate operational choice (everything runs on CPU for now) and also sidesteps
    the Apple-MPS crash: transformers' ``rt_detr_v2`` hardcodes float64 in its sin/cos
    position embedding, which MPS rejects (the same wall poc-insurance-chat's
    ``_resolve_accelerator_device`` works around by coercing to CPU on darwin).

    OCR, when enabled, runs through the installed Tesseract binary (CLI engine) so
    the system ``deu``/``eng`` language data is used; point ``TESSDATA_PREFIX`` at the
    directory holding ``deu.traineddata`` (e.g. the repo-local ``.tessdata/``).
    Env knobs:
      ``DOCLING_DO_OCR``   1|0 (default 0 — text-layer PDFs need no OCR)
      ``DOCLING_OCR_LANG`` comma list (default ``deu,eng``) when OCR is on

    Raises on empty extraction so the caller falls back to the next converter.
    """
    from docling.datamodel.accelerator_options import AcceleratorDevice, AcceleratorOptions
    from docling.datamodel.base_models import InputFormat
    from docling.datamodel.pipeline_options import (
        PdfPipelineOptions,
        TableFormerMode,
        TesseractCliOcrOptions,
    )
    from docling.document_converter import DocumentConverter, PdfFormatOption

    # CPU-only by design — nothing on GPU/MPS for now.
    device = AcceleratorDevice.CPU
    do_ocr = os.getenv("DOCLING_DO_OCR", "0").strip().lower() in ("1", "true", "yes")

    opts = PdfPipelineOptions()
    opts.do_ocr = do_ocr
    opts.do_table_structure = True
    opts.table_structure_options.mode = TableFormerMode.ACCURATE
    if do_ocr:
        langs = [
            s.strip() for s in os.getenv("DOCLING_OCR_LANG", "deu,eng").split(",") if s.strip()
        ]
        # CLI engine -> uses the system `tesseract` binary, which honours TESSDATA_PREFIX.
        opts.ocr_options = TesseractCliOcrOptions(lang=langs)
    opts.accelerator_options = AcceleratorOptions(device=device)

    converter = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
    )
    md = converter.convert(pdf_path).document.export_to_markdown()
    if not md or not md.strip():
        raise RuntimeError(f"docling produced empty output for {pdf_path}")
    return normalize_dashes(_relevel_headings(md))


def pdf_markdown_converters() -> list[tuple[str, Callable[[str], str]]]:
    """Ordered ``(name, fn)`` PDF->markdown converters, per the ``PDF_CONVERTER`` env.

    INDEX-01: ``pymupdf4llm`` (AGPL, fast, default) and ``docling`` (MIT,
    layout-aware, German-ligature-correct — the RFC-003 D3 / HR4 residency escape).
    The caller tries them in order and only falls back to ``page_index`` when all
    markdown converters fail. ``docling`` is listed only when importable, so a base
    install without the ``docling`` extra degrades to ``pymupdf4llm`` cleanly.

    ``docling`` is the **default** primary (it is ligature-correct on the German
    vertical and MIT-licensed, lowering AGPL exposure); set
    ``PDF_CONVERTER=pymupdf4llm`` to make the faster AGPL route primary instead, in
    which case Docling becomes the secondary markdown attempt.
    """
    import importlib.util

    primary = os.getenv("PDF_CONVERTER", "docling").strip().lower()
    have_docling = importlib.util.find_spec("docling") is not None
    chain: list[tuple[str, Callable[[str], str]]] = [("pymupdf4llm", pdf_to_markdown)]
    if have_docling:
        if primary == "docling":
            chain.insert(0, ("docling", pdf_to_markdown_docling))
        else:
            chain.append(("docling", pdf_to_markdown_docling))
    elif primary == "docling":
        logger.warning(
            "PDF_CONVERTER=docling but docling is not installed; install the "
            "'docling' extra (uv sync --extra docling). Falling back to pymupdf4llm."
        )
    return chain


def libreoffice_to_pdf(input_path: str) -> str:
    """Convert a DOCX/PPTX file to PDF via LibreOffice headless.

    Returns the path to the generated PDF in a temporary directory.
    The caller is responsible for cleaning up the parent directory:
        shutil.rmtree(os.path.dirname(pdf_path), ignore_errors=True)
    """
    lo = shutil.which("libreoffice") or shutil.which("soffice")
    if not lo:
        raise RuntimeError(
            "LibreOffice not found. Install libreoffice-headless and ensure it is on PATH."
        )
    outdir = tempfile.mkdtemp(prefix="lo_pdf_")
    # Each conversion gets its own profile dir so parallel invocations don't conflict.
    profile_dir = os.path.join(outdir, "lo_profile")
    os.makedirs(profile_dir, exist_ok=True)
    try:
        result = subprocess.run(
            [
                lo,
                f"-env:UserInstallation=file://{profile_dir}",
                "--headless",
                "--convert-to", "pdf",
                "--outdir", outdir,
                input_path,
            ],
            capture_output=True,
            text=True,
            timeout=180,
        )
        stem = os.path.splitext(os.path.basename(input_path))[0]
        pdf_path = os.path.join(outdir, f"{stem}.pdf")
        # Check for the PDF first; a non-zero exit may be a recoverable warning
        if not os.path.isfile(pdf_path):
            pdfs = [f for f in os.listdir(outdir) if f.endswith(".pdf")]
            if pdfs:
                pdf_path = os.path.join(outdir, pdfs[0])
            elif result.returncode != 0:
                raise RuntimeError(
                    f"LibreOffice conversion failed (exit {result.returncode}): {result.stderr.strip()}"
                )
            else:
                raise RuntimeError("LibreOffice did not produce a PDF file.")
        return pdf_path
    except Exception:
        shutil.rmtree(outdir, ignore_errors=True)
        raise


async def html_to_markdown_with_images(path: str, model: str) -> str:
    """Convert an HTML file to markdown, replacing <img> tags with vision-API descriptions.

    Images are described concurrently via the OpenAI vision API and inserted as
    [Image: <description>] markers at the position of the original <img> tag.
    """
    import html2text

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        html_content = f.read()

    img_pattern = re.compile(r"<img[^>]+src=[\"']([^\"']+)[\"'][^>]*/?>", re.IGNORECASE)
    srcs = img_pattern.findall(html_content)

    async def _describe(src: str) -> str:
        try:
            from .config import get_openai_client
            client = get_openai_client()
            response = await client.chat.completions.create(
                model=model,
                messages=[
                    {
                        "role": "user",
                        "content": [
                            {"type": "image_url", "image_url": {"url": src}},
                            {
                                "type": "text",
                                "text": "Describe this image concisely in 1-2 sentences for document context.",
                            },
                        ],
                    }
                ],
                max_tokens=150,
            )
            return response.choices[0].message.content.strip()
        except Exception:
            return "image"

    descriptions = await asyncio.gather(*(_describe(src) for src in srcs))

    counter = iter(range(len(descriptions)))

    def _replace(match: re.Match) -> str:
        i = next(counter, None)
        desc = descriptions[i] if i is not None else "image"
        return f"[Image: {desc}]"

    modified_html = img_pattern.sub(_replace, html_content)

    h = html2text.HTML2Text()
    h.ignore_images = True
    h.ignore_links = False
    h.body_width = 0
    return normalize_dashes(h.handle(modified_html))


def flatten_nodes(nodes: list, results: list, query_lower: str) -> None:
    """Recursively walk PageIndex tree nodes and collect keyword matches in-place."""
    for node in nodes:
        title   = node.get("title", "")
        summary = node.get("summary", "")
        text    = node.get("text", "")
        if query_lower in title.lower() or query_lower in summary.lower() or query_lower in text.lower():
            results.append({
                "node_id":     node.get("node_id"),
                "title":       title,
                "summary":     summary,
                "start_index": node.get("start_index"),
                "end_index":   node.get("end_index"),
            })
        child_nodes = node.get("nodes", [])
        if child_nodes:
            flatten_nodes(child_nodes, results, query_lower)


def docx_to_markdown(path: str) -> str:
    """Convert a DOCX file to a markdown string preserving heading hierarchy."""
    from docx import Document
    doc = Document(path)
    lines = []
    heading_map = {
        "Heading 1": "#", "Heading 2": "##", "Heading 3": "###",
        "Heading 4": "####", "Heading 5": "#####", "Heading 6": "######",
    }
    for para in doc.paragraphs:
        text = para.text.strip()
        if not text:
            lines.append("")
            continue
        prefix = next((v for k, v in heading_map.items() if para.style.name.startswith(k)), None)
        lines.append(f"{prefix} {text}" if prefix else text)
    return normalize_dashes("\n".join(lines))


def pptx_to_markdown(path: str) -> str:
    """Convert a PPTX file to markdown, one H1 section per slide."""
    from pptx import Presentation
    prs = Presentation(path)
    lines = []
    for i, slide in enumerate(prs.slides, 1):
        title_shape = slide.shapes.title
        title = title_shape.text.strip() if title_shape and title_shape.text.strip() else f"Slide {i}"
        lines.append(f"# {title}")
        for shape in slide.shapes:
            if shape == title_shape or not shape.has_text_frame:
                continue
            for para in shape.text_frame.paragraphs:
                text = para.text.strip()
                if text:
                    lines.append(text)
        lines.append("")
    return normalize_dashes("\n".join(lines))
