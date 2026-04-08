"""Document format conversion helpers and tree search utilities."""

import asyncio
import os
import re
import shutil
import subprocess
import tempfile


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
    import openai

    with open(path, "r", encoding="utf-8", errors="replace") as f:
        html_content = f.read()

    img_pattern = re.compile(r"<img[^>]+src=[\"']([^\"']+)[\"'][^>]*/?>", re.IGNORECASE)
    srcs = img_pattern.findall(html_content)

    async def _describe(src: str) -> str:
        try:
            from .config import settings
            client = openai.AsyncOpenAI(
                api_key=settings.openai_api_key,
                base_url=settings.openai_base_url,
            )
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
    return h.handle(modified_html)


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
    return "\n".join(lines)


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
    return "\n".join(lines)
