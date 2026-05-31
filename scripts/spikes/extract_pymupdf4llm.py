#!/usr/bin/env python3
"""
Spike: Extract German insurance T&C PDFs using pymupdf4llm.
Outputs markdown to /tmp/docling_spike/pymupdf/
This is a throwaway investigation script — do NOT import from src/.
"""
import sys
import os
import time
import pymupdf4llm

DATA_DIR = "/Users/saliltrehan/Documents/Python_n_R/Personal/pageindex/issue/data"
OUT_DIR = "/tmp/docling_spike/pymupdf"

os.makedirs(OUT_DIR, exist_ok=True)

PDFS = [
    "AKB.pdf.pdf",
    "AVB-PHV-Basis.pdf.pdf",
    "AVB-PHV-Komfort.pdf.pdf",
    "AVB-PHV-Premium.pdf.pdf",
]

for pdf_name in PDFS:
    pdf_path = os.path.join(DATA_DIR, pdf_name)
    out_name = pdf_name.replace(".pdf.pdf", ".md")
    out_path = os.path.join(OUT_DIR, out_name)

    print(f"Extracting: {pdf_name} ...", flush=True)
    t0 = time.time()
    try:
        md_text = pymupdf4llm.to_markdown(pdf_path)
        elapsed = time.time() - t0
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(md_text)
        word_count = len(md_text.split())
        char_count = len(md_text)
        print(f"  -> OK: {char_count} chars, {word_count} words, {elapsed:.2f}s => {out_path}")
    except Exception as e:
        print(f"  -> ERROR: {e}")

print("\nDone. Outputs in:", OUT_DIR)
