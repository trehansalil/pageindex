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

_DASH_TRANSLATION = {
    0x2010: "-",  # hyphen
    0x2011: "-",  # non-breaking hyphen — used in PHV clause codes (e.g. A1‑6.1)
    0x2013: "-",  # en-dash
    0x2014: "-",  # em-dash
    0x2212: "-",  # minus sign
}


def normalize_dashes(s: str) -> str:
    """Map Unicode hyphen/dash variants to ASCII '-' (CONV-01-C2).

    Includes the non-breaking hyphen (U+2011) the German PHV PDFs use inside
    clause codes like ``A1‑6.1``; normalising it lets numbering-depth recovery
    (``numbering_depth``) parse those codes."""
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


# --- Heading-depth recovery from German-insurance numbering schemes -----------
# Docling's export_to_markdown() renders every section header at a single
# '#'-level, so even when the docling-hierarchical-pdf add-on selects the right
# headings the resulting tree is flat (depth 1) and fails the depth>=2 quality
# gate (HR5 / validate_tree). We re-derive each heading's depth from its
# numbering prefix. Two schemes appear in the validated German insurance corpus
# (2026-05-31):
#   dot notation   (e.g. AKB):  "A.1" -> 2, "A.1.1" -> 3   (bare "A" stays H1)
#   hyphen clauses (e.g. PHV):  "Abschnitt A1" -> 2, "A1-6" -> 3, "A1-6.1" -> 4
# numbering_depth() returns None when no scheme is recognised so generic /
# non-numbered documents keep their existing heading levels untouched.
_HLINE_RE = re.compile(r"^#{1,6}[ \t]+(.*\S)[ \t]*$", re.MULTILINE)
_NUM_SECTION_WORD_RE = re.compile(r"^Abschnitt\s+[A-Z]?\d", re.IGNORECASE)
_NUM_PART_RE = re.compile(r"^(?:Teil|Anhang|Kapitel|Abschnitt)\b", re.IGNORECASE)
_NUM_HYPHEN_RE = re.compile(r"^[A-Z]\d+-\d+(\.\d+)?(?=[ \t]|$)")
_NUM_DOT_RE = re.compile(r"^[A-Z](\.\d+)+(?=[ \t.:]|$)")
_NUM_PARA_RE = re.compile(r"^(?:§\s*)?\d+(\.\d+)+(?=[ \t.:]|$)")


def numbering_depth(title: str) -> int | None:
    """Infer a 1-based heading depth from a German-insurance numbering prefix.

    Returns None when no recognised numbering scheme is present, so the caller
    leaves such headings at their existing level."""
    t = title.strip()
    # "Abschnitt A1" is a section nested one level under its "Teil".
    if _NUM_SECTION_WORD_RE.match(t):
        return 2
    # Part / appendix words sit at the top.
    if _NUM_PART_RE.match(t):
        return 1
    # Hyphen clauses: "A1-6" -> 3, "A1-6.1" -> 4.
    if _NUM_HYPHEN_RE.match(t):
        return 4 if "." in t.split()[0] else 3
    # Dot notation: "A.1" -> 2, "A.1.1" -> 3.
    m = _NUM_DOT_RE.match(t)
    if m:
        return 1 + m.group(0).count(".")
    # Plain paragraph / numeric sub-sections: "3.1" -> 2, "§3.1.2" -> 3.
    m = _NUM_PARA_RE.match(t)
    if m:
        return 1 + m.group(0).count(".")
    return None


def _relevel_by_numbering(md: str) -> str:
    """Override each markdown heading's '#'-level from its numbering prefix.

    Headings whose title has no recognised numbering prefix are left unchanged,
    so this is safe to run after ``_relevel_headings`` on any corpus."""
    def repl(m: "re.Match[str]") -> str:
        title = m.group(1)
        depth = numbering_depth(title)
        if depth is None:
            return m.group(0)
        # Clamp to the markdown heading range [1, 6]; deeply nested numbered
        # sections (e.g. "A.1.1.1.1.1") would otherwise emit 7+ '#'s, which is
        # not a valid heading. Mirrors the clamp in _relevel_by_containment.
        return "#" * max(1, min(6, depth)) + " " + title
    return _HLINE_RE.sub(repl, md)


# --- Heading-depth recovery by numbering-prefix CONTAINMENT (no per-scheme table) -
# Every STRUCTURAL depth signal Docling exposes on this corpus is flat:
# SectionHeaderItem.level, body-tree traversal depth, and the PDF outline are all
# level 1 (verified 2026-05-31). So depth must be inferred from the heading TEXT.
# We segment each heading's leading numbering LABEL into atomic components
# ("A.1.1"->[A,1,1]; "Abschnitt A1"->[A,1]; bare prose title -> []) and set
# depth = 1 + length of the longest OTHER present label that is a proper prefix.
# This nests an unseen numbering style without a hardcoded regex table, so it is
# the PRIMARY depth source; numbering_depth() above is kept only as a last-resort
# fallback for the degenerate case where containment stays flat.

# Structural words that introduce a numbering label. We collapse the spaced-out
# Docling rendering ("T e i l   A") before matching, so the regex sees "TeilA".
_WORD_RE = re.compile(
    r"^(teil|anhang|abschnitt|kapitel)\b", re.IGNORECASE
)


def _collapse_spaced(text: str) -> str:
    """Collapse Docling's letter-spaced headings: 'T e i l   A' -> 'Teil A'.

    Docling renders these with SINGLE spaces between the letters of a word and a
    WIDER gap (2+ spaces) between words. We split on runs of 2+ spaces to recover
    word boundaries, then glue single-spaced letters inside each chunk. A '-'
    surrounded by spaces is kept as a separator word. Ordinary headings (whose
    tokens are multi-letter) are returned unchanged.
    """
    raw_toks = text.split()
    if not (
        len(raw_toks) >= 4
        and sum(1 for t in raw_toks if len(t) == 1) >= len(raw_toks) * 0.6
    ):
        return text
    # split on 2+ spaces -> word-level chunks; within a chunk, single chars glue.
    chunks = re.split(r"\s{2,}", text.strip())
    out = []
    for chunk in chunks:
        ctoks = chunk.split()
        buf = []
        for t in ctoks:
            if t == "-":
                # A '-' inside a chunk is a label separator (e.g. clause code
                # "A1-6.1" rendered spaced as "A 1 - 6 . 1"): glue it to the
                # surrounding letters with NO spaces so the clause code stays
                # intact. Surrounding it with spaces would break hyphenated
                # clause-code detection ("A1-6.1" -> "A1- 6.1").
                buf.append("-")
            else:
                buf.append(t)
        if buf:
            out.append("".join(buf))
    return " ".join(out)


def _split_alnum(tok: str) -> list[str]:
    """Split an alnum label token at every letter<->digit boundary and (..) group.

    "A1"   -> ["A","1"]      "A(GB)" -> ["A","GB"]      "B4" -> ["B","4"]
    "A"    -> ["A"]          "A(GB)1"-> ["A","GB","1"]
    """
    # pull out a parenthesised group first
    parts: list[str] = []
    m = re.match(r"^([A-Za-z]+)(?:\(([A-Za-z0-9]+)\))?(\d+)?$", tok)
    if m:
        if m.group(1):
            parts.append(m.group(1))
        if m.group(2):
            parts.append(m.group(2))
        if m.group(3):
            parts.append(m.group(3))
        return parts
    # fallback: generic letter/digit run split
    return [p for p in re.findall(r"[A-Za-z]+|\d+", tok)]


def _segment_label(title: str) -> list[str]:
    """Segment a heading's leading numbering label into atomic components.

    Returns [] when the heading carries no recognisable label (a bare title).
    Word-prefix rule: a leading structural word (Teil/Anhang/Abschnitt/Kapitel)
    is consumed; the label that follows it is what nests. "Teil A"->[A];
    "Abschnitt A1"->[A,1]; "A1-6.1"->[A,1,6,1]; "A.1.1"->[A,1,1];
    "A(GB)-1"->[A,GB,1]; "Versicherte Personen"->[].
    """
    t = _collapse_spaced(title.strip())
    # consume an optional leading structural word
    wm = _WORD_RE.match(t)
    if wm:
        t = t[wm.end():].lstrip(" -")
    # the label is the leading run of [alnum . - ( )] up to the first space
    # that starts the descriptive title. Grab the first whitespace-delimited tok.
    head = t.split(maxsplit=1)[0] if t else ""
    if not head:
        return []
    # The label may itself contain '.', '-' and '()' separators.
    # Stop the label at a separator that is followed by a non-label char? Simpler:
    # the head token IS the label candidate. Validate it starts with a letter
    # and contains at least one alnum.
    # Strip trailing punctuation like ':' or '.' used as a terminator? Keep dots
    # that are internal (A.1.) — drop a single trailing '.'/':'.
    head = head.rstrip(":")
    # A label must begin with a letter (clause code "A1-6.1") OR a digit
    # (numeric section "3.1") to be a recognisable numbering label. Rejecting
    # digit-led heads here would drop numeric headings before the digit-aware
    # validation below, leaving that part of the hierarchy flat.
    if not re.match(r"^[A-Za-z0-9]", head):
        return []
    comps: list[str] = []
    # split on the structural separators '.', '-'
    for seg in re.split(r"[.\-]", head):
        seg = seg.strip()
        if not seg:
            continue
        sub = _split_alnum(seg)
        if not sub:
            return []  # contains a non-alnum chunk we don't understand -> no label
        comps.extend(sub)
    # Reject degenerate labels: a single letter that is actually a word start
    # is fine ("A"), but require the whole head to be alnum/sep only — if the
    # head had spaces stripped we already isolated one token, so this holds.
    # Guard: a pure single-letter label is valid (top section).
    if not comps:
        return []
    # Reject labels that are clearly prose (e.g. first word "Was", "Wer"): a real
    # label is short and its first component is a single letter OR is all digits.
    first = comps[0]
    if not (re.fullmatch(r"[A-Za-z]", first) or first.isdigit()):
        return []
    # And the whole token must be short-ish (a clause code, not a sentence word).
    if len(head) > 14:
        return []
    return comps


def _containment_depths(titles: list[str]) -> list[int | None]:
    """Depth of each heading via numbering-prefix containment (grammar inference).

    depth(i) = 1 + length of the longest OTHER label that is a proper prefix of
    label(i). Bare-title headings (label == []) return None so the caller leaves
    that heading's existing level untouched."""
    labels = [_segment_label(t) for t in titles]
    label_set = [tuple(l) for l in labels]
    present = set(l for l in label_set if l)
    depths: list[int | None] = []
    for lab in label_set:
        if not lab:
            depths.append(None)
            continue
        # longest proper prefix that is itself a present label
        best = 0
        for k in range(len(lab) - 1, 0, -1):
            if lab[:k] in present:
                best = k
                break
        depths.append(best + 1)
    return depths


def _relevel_by_containment(md: str) -> str:
    """Override each markdown heading's '#'-level from numbering-prefix containment.

    Headings whose title carries no label (containment depth None) are left
    exactly as-is, so this is safe to run after ``_relevel_headings`` on any
    corpus. Non-heading text and spacing are preserved verbatim."""
    matches = list(_HLINE_RE.finditer(md))
    if not matches:
        return md
    depths = _containment_depths([m.group(1) for m in matches])
    out: list[str] = []
    pos = 0
    for m, depth in zip(matches, depths):
        out.append(md[pos:m.start()])
        if depth is None:
            out.append(m.group(0))  # no label -> keep existing level
        else:
            out.append("#" * max(1, min(6, depth)) + " " + m.group(1))
        pos = m.end()
    out.append(md[pos:])
    return "".join(out)


def _max_heading_level(md: str) -> int:
    """Largest markdown heading '#'-run length present, or 0 if none."""
    levels = [len(m.group(1)) for m in _HEADING_RE.finditer(md)]
    return max(levels) if levels else 0


def _repromote_numbered_headings(doc) -> int:
    """Re-promote demoted body TextItems back to headings (no hardcoding).

    The docling-hierarchical-pdf add-on gives a clean heading SELECTION but
    over-prunes: it demotes deep numbered clauses (AKB "A.1.1", "A.1.1.1") to
    body TextItems alongside the font-size junk, capping the tree's depth. This
    walks the post-add-on doc and converts a TextItem back to a SectionHeaderItem
    IFF its numbering label is a proper NUMERIC EXTENSION of a kept-section label:
    there is a non-empty kept-section label P that is a proper prefix of the
    item's label and every component beyond P is a pure digit run ("A.1.1" =
    kept "A.1" + ["1"] -> promote; list marker "a" or mis-segmented prose
    "Fuehren"->[F,hren] -> NOT promoted). The anchors are the add-on's OWN kept
    section labels — nothing is hardcoded. Mutates the doc model in place (so body
    text is preserved for export) using the add-on's set_item_in_doc pattern, and
    returns the number of promotions."""
    from docling_core.types.doc.document import SectionHeaderItem, TextItem

    def label_of(item) -> tuple:
        return tuple(_segment_label(normalize_dashes((item.text or "").strip())))

    # Pass 1: trusted anchors = the add-on's kept section labels (non-empty).
    anchors: set[tuple] = set()
    for item, _ in doc.iterate_items(with_groups=False):
        if isinstance(item, SectionHeaderItem):
            lab = label_of(item)
            if lab:
                anchors.add(lab)

    # Pass 2: promote demoted TextItems whose label numerically extends an anchor.
    n_promo = 0
    for item, _ in list(doc.iterate_items(with_groups=False)):
        if isinstance(item, SectionHeaderItem) or not isinstance(item, TextItem):
            continue
        lab = label_of(item)
        if not lab:
            continue
        if any(
            lab[:k] in anchors and all(c.isdigit() for c in lab[k:])
            for k in range(len(lab) - 1, 0, -1)
        ):
            # TextItem -> SectionHeaderItem, then swap in at its self_ref index
            # (the add-on's set_item_in_doc pattern).
            header = SectionHeaderItem(**{
                k: v for k, v in item.model_dump().items()
                if k != "label" and k in SectionHeaderItem.model_fields
            })
            _, path, idx = item.self_ref.split("/")
            getattr(doc, path)[int(idx)] = header
            n_promo += 1
    return n_promo


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
      ``DOCLING_ARTIFACTS_PATH`` dir of pre-downloaded model weights for offline use
        (set in the container image; unset locally -> weights fetched from HF on first use)

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
    # Use pre-baked model artifacts when available (set in the container image so
    # egress-limited workers never download weights at runtime — a download failure
    # there would otherwise raise and silently fall back to pymupdf4llm -> flat tree
    # -> depth<2). Unset (local dev) -> docling fetches from HF on first use.
    artifacts_path = os.getenv("DOCLING_ARTIFACTS_PATH", "").strip()
    if artifacts_path:
        opts.artifacts_path = artifacts_path

    converter = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
    )
    result = converter.convert(pdf_path)

    # docling-hierarchical-pdf (krrome) rebuilds heading SELECTION from the PDF
    # outline/numbering, dropping the font-size false positives Docling otherwise
    # emits as headings (page numbers, letter-spaced body text, clause fragments).
    # Validated on the German corpus 2026-05-31: cuts noisy headings 34-94%.
    # Optional + third-party (single-maintainer) — never let it break ingestion;
    # degrade to raw Docling headings on any failure.
    try:
        from hierarchical.postprocessor import ResultPostprocessor

        ResultPostprocessor(result, source=pdf_path).process()
    except ImportError:
        logger.warning(
            "docling-hierarchical-pdf not installed; using raw docling headings. "
            "Install it to recover clean heading selection."
        )
    except Exception as exc:  # noqa: BLE001 — add-on must never be fatal
        logger.warning(
            "hierarchical add-on postprocess failed for %s (%s); using raw docling headings",
            pdf_path, exc,
        )

    # Re-promote the deep numbered clauses the add-on demoted to body text
    # (e.g. AKB "A.1.1"/"A.1.1.1"), restoring the tree depth the add-on prunes.
    # Same defensive contract as the add-on: re-promotion must NEVER be fatal —
    # on any failure degrade to the add-on's selection.
    try:
        n_promo = _repromote_numbered_headings(result.document)
        if n_promo > 0:
            logger.info(
                "re-promoted %d demoted numbered clause(s) to headings for %s",
                n_promo, pdf_path,
            )
    except Exception as exc:  # noqa: BLE001 — re-promotion must never be fatal
        logger.warning(
            "heading re-promotion failed for %s (%s); using add-on selection",
            pdf_path, exc,
        )

    md = result.document.export_to_markdown()
    if not md or not md.strip():
        raise RuntimeError(f"docling produced empty output for {pdf_path}")
    # Normalise dashes BEFORE depth recovery so hyphen clause codes (A1-6.1) parse;
    # _relevel_headings sets the baseline (shallowest -> H1), then
    # _relevel_by_containment re-derives depth from each heading's numbering-prefix
    # CONTAINMENT (the primary depth source — no per-scheme table) recovering the
    # depth>=2 the HR5 gate needs. numbering_depth's per-scheme regex is the
    # last-resort FALLBACK, applied only if containment stayed degenerately flat.
    md = _relevel_by_containment(_relevel_headings(normalize_dashes(md)))
    if _max_heading_level(md) < 2:
        md = _relevel_by_numbering(md)  # last-resort fallback only if containment stayed flat
    return md


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
            from .client import get_openai_client
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
