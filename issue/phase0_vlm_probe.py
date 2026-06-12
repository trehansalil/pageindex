"""RFC-004 Phase 0 — throwaway VLM hierarchy-recovery probe (NOT committed to the
pipeline; gates the VLM-01 track). Renders the 5 firing pages at 144 & 200 DPI via
pypdfium2 (BSD-3/Apache-2, HR4-clean) and asks a VLM to propose a heading hierarchy.

Engines:
  - gpt-4.1 vision via the OpenAI API (OPENAI_API_KEY). Always runs.
  - Granite-Docling-258M on CPU (HF transformers). Runs only with --granite.

HR3 note: the firing docs are PUBLIC German insurance product T&Cs (Reitlehrer /
Unfall-Leistungsuebersicht / GHV-TKV-Tarif), not customer PII, and OPENAI_BASE_URL
defaults to the non-ZDR api.openai.com. This is a throwaway LOCAL experiment only;
production VLM routing must go through the ZDR lever (D6) before any real corpus.

Usage:
  uv run python issue/phase0_vlm_probe.py            # gpt-4.1 only
  uv run python issue/phase0_vlm_probe.py --granite  # + Granite-258M CPU
"""

from __future__ import annotations

import base64
import io
import json
import os
import sys
import time
import traceback

import pypdfium2 as pdfium
from dotenv import load_dotenv

load_dotenv()

HERE = os.path.dirname(os.path.abspath(__file__))
DATA = os.path.join(HERE, "data")
OUT = os.path.join(HERE, "phase0_results.json")

# (filename, class, today's gate reason, page index 0-based to probe)
FIRING = [
    ("Reitlehrer - Bereiter.pdf", "A/B (0 nodes)", "node_count<3", 0),
    ("Reitlehrer - Bereiter - Kutschfahrlehrer.pdf", "A/B (0 nodes)", "node_count<3", 0),
    ("GHV-TKV-Tarif.pdf", "A (flat grid, 1 node)", "node_count<3", 0),
    ("Reiter-Unfallversicherung-Leistungsuebersicht-2025-001.pdf.pdf", "B (4 nodes)", "depth<2", 0),
    ("Unfallversicherung-Leistungsuebersicht-2025-001.pdf.pdf", "B (6 nodes)", "depth<2", 0),
]

DPIS = [144, 200]

PROMPT = (
    "You are a document-structure analyzer for German insurance T&C PDFs. "
    "Look at this page image and extract its VISUAL heading hierarchy. "
    "Return ONLY JSON: {\"headings\":[{\"text\":\"...\",\"level\":1},...], "
    "\"is_flat\":bool, \"max_depth\":int, \"note\":\"...\"}. "
    "Rules: level 1 = top section, 2 = subsection, etc. Numbered clauses like "
    "'1', '1.1', '2', '2.1' ARE a 2-level hierarchy. Table/grid COLUMN LABELS and "
    "navigation links are NOT hierarchy -> set is_flat=true. Transcribe heading "
    "text VERBATIM including German characters (ä ö ü ß) and the fl/fi ligatures."
)


def render_page(pdf_path: str, page_idx: int, dpi: int) -> bytes:
    doc = pdfium.PdfDocument(pdf_path)
    try:
        page = doc[page_idx]
        pil = page.render(scale=dpi / 72.0).to_pil()
        buf = io.BytesIO()
        pil.save(buf, format="PNG")
        return buf.getvalue()
    finally:
        doc.close()


def probe_gpt(png: bytes, model: str = "gpt-4.1") -> dict:
    from openai import OpenAI

    client = OpenAI()
    b64 = base64.b64encode(png).decode()
    t0 = time.time()
    resp = client.chat.completions.create(
        model=model,
        temperature=0,
        response_format={"type": "json_object"},
        messages=[
            {
                "role": "user",
                "content": [
                    {"type": "text", "text": PROMPT},
                    {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{b64}"}},
                ],
            }
        ],
    )
    dt = round(time.time() - t0, 2)
    raw = resp.choices[0].message.content or "{}"
    try:
        parsed = json.loads(raw)
    except json.JSONDecodeError:
        parsed = {"_parse_error": True, "_raw": raw[:500]}
    return {"latency_s": dt, "finish_reason": resp.choices[0].finish_reason, "result": parsed}


def probe_granite(png: bytes) -> dict:
    """Granite-Docling-258M on CPU via transformers. Heavy; first run downloads ~weights."""
    import resource

    import torch
    from PIL import Image
    from transformers import AutoProcessor

    try:  # newer transformers canonical class for image-text-to-text models
        from transformers import AutoModelForImageTextToText as _VlmModel
    except ImportError:  # older transformers
        from transformers import AutoModelForVision2Seq as _VlmModel

    model_id = "ibm-granite/granite-docling-258M"
    t0 = time.time()
    proc = AutoProcessor.from_pretrained(model_id)
    model = _VlmModel.from_pretrained(model_id, torch_dtype=torch.float32)
    model.eval()
    img = Image.open(io.BytesIO(png)).convert("RGB")
    msgs = [{"role": "user", "content": [{"type": "image"}, {"type": "text", "text": "Convert this page to docling."}]}]
    prompt = proc.apply_chat_template(msgs, add_generation_prompt=True)
    inputs = proc(text=prompt, images=[img], return_tensors="pt")
    with torch.no_grad():
        out = model.generate(**inputs, max_new_tokens=2048)
    text = proc.batch_decode(out, skip_special_tokens=False)[0]
    dt = round(time.time() - t0, 2)
    # Count DocTags section_header_level_N markers — the hierarchy signal.
    import re

    levels = re.findall(r"section_header_level_(\d+)", text)
    peak_rss_mb = round(resource.getrusage(resource.RUSAGE_SELF).ru_maxrss / (1024 * 1024), 1)
    return {
        "latency_s": dt,
        "peak_rss_mb": peak_rss_mb,  # macOS ru_maxrss is bytes; Q3 CPU-RSS data point
        "doctags_excerpt": text[:1500],
        "section_header_levels": sorted({int(x) for x in levels}),
        "n_headers": len(levels),
    }


def main() -> None:
    do_granite = "--granite" in sys.argv
    if not os.environ.get("OPENAI_API_KEY"):
        print("FATAL: OPENAI_API_KEY not set", file=sys.stderr)
        sys.exit(1)

    results: list[dict] = []
    for fname, klass, reason, page_idx in FIRING:
        path = os.path.join(DATA, fname)
        entry: dict = {"doc": fname, "class": klass, "gate_reason": reason, "page": page_idx}
        if not os.path.exists(path):
            entry["error"] = "FILE_NOT_FOUND"
            results.append(entry)
            print(f"[SKIP] {fname}: not found")
            continue
        for dpi in (DPIS if not os.environ.get("PHASE0_GRANITE_ONLY") else []):
            tag = f"gpt_{dpi}dpi"
            try:
                png = render_page(path, page_idx, dpi)
                entry[tag] = probe_gpt(png)
                r = entry[tag]["result"]
                print(f"[OK] {fname} @ {dpi}dpi: is_flat={r.get('is_flat')} "
                      f"max_depth={r.get('max_depth')} headings={len(r.get('headings', []))}")
            except Exception as e:  # throwaway probe — capture and continue
                entry[tag] = {"error": repr(e), "tb": traceback.format_exc()[-800:]}
                print(f"[ERR] {fname} @ {dpi}dpi: {e!r}")
        if do_granite:
            try:
                png = render_page(path, page_idx, 200)
                entry["granite_200dpi"] = probe_granite(png)
                g = entry["granite_200dpi"]
                print(f"[OK] {fname} granite: levels={g['section_header_levels']} n={g['n_headers']}")
            except Exception as e:
                entry["granite_200dpi"] = {"error": repr(e), "tb": traceback.format_exc()[-800:]}
                print(f"[ERR] {fname} granite: {e!r}")
        results.append(entry)
        with open(OUT, "w") as f:
            json.dump(results, f, ensure_ascii=False, indent=2)

    print(f"\n=== wrote {OUT} ({len(results)} docs) ===")


if __name__ == "__main__":
    main()
