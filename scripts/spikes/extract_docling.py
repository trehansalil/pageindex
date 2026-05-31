#!/usr/bin/env python3
"""
Spike: Extract German insurance T&C PDFs using Docling.
Outputs markdown to /tmp/docling_spike/docling/
This is a throwaway investigation script — do NOT import from src/.
Run with: /tmp/docling_venv/bin/python scripts/spikes/extract_docling.py
"""
import os
import time

DATA_DIR = "/Users/saliltrehan/Documents/Python_n_R/Personal/pageindex/issue/data"
OUT_DIR = "/tmp/docling_spike/docling"

os.makedirs(OUT_DIR, exist_ok=True)

PDFS = [
    "AKB.pdf.pdf",
    "AVB-PHV-Basis.pdf.pdf",
    "AVB-PHV-Komfort.pdf.pdf",
    "AVB-PHV-Premium.pdf.pdf",
]

from docling.document_converter import DocumentConverter
from docling.datamodel.base_models import InputFormat
from docling.datamodel.pipeline_options import PdfPipelineOptions
from docling.document_converter import PdfFormatOption

print("Initializing Docling converter...", flush=True)
t_init = time.time()

pipeline_options = PdfPipelineOptions()
pipeline_options.do_ocr = False  # Use native PDF text extraction first
pipeline_options.do_table_structure = True

converter = DocumentConverter(
    format_options={
        InputFormat.PDF: PdfFormatOption(pipeline_options=pipeline_options)
    }
)
print(f"Converter initialized in {time.time() - t_init:.2f}s", flush=True)

for pdf_name in PDFS:
    pdf_path = os.path.join(DATA_DIR, pdf_name)
    out_name = pdf_name.replace(".pdf.pdf", ".md")
    out_path = os.path.join(OUT_DIR, out_name)

    print(f"\nExtracting: {pdf_name} ...", flush=True)
    t0 = time.time()
    try:
        result = converter.convert(pdf_path)
        md_text = result.document.export_to_markdown()
        elapsed = time.time() - t0
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(md_text)
        word_count = len(md_text.split())
        char_count = len(md_text)
        print(f"  -> OK: {char_count} chars, {word_count} words, {elapsed:.2f}s => {out_path}", flush=True)
    except Exception as e:
        import traceback
        print(f"  -> ERROR: {e}", flush=True)
        traceback.print_exc()

print("\nDone. Outputs in:", OUT_DIR)
