"""Reproduce the Katzen-Kranken depth<2 failure and capture heading evolution.

Runs the full Docling pipeline once, dumping markdown at each stage to /tmp so the
pure depth-recovery functions can be iterated on cheaply afterward.
"""
import os
import re
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from pageindex_mcp import converters as C  # noqa: E402

PDF = os.path.join(
    os.path.dirname(__file__), "data",
    "Katzen-Kranken-Besondere-Bedingungen-2024-002.pdf.pdf",
)

HEAD = re.compile(r"^(#{1,6})[ \t]+(.*\S)[ \t]*$", re.MULTILINE)


def heads(md: str):
    return [(len(m.group(1)), m.group(2)) for m in HEAD.finditer(md)]


def dump(tag: str, md: str):
    path = f"/tmp/katzen_{tag}.md"
    with open(path, "w") as f:
        f.write(md)
    hs = heads(md)
    levels = sorted({lv for lv, _ in hs})
    print(f"\n===== {tag}: {len(hs)} headings, levels={levels}, max={max([lv for lv,_ in hs] or [0])} -> {path}")
    for lv, t in hs:
        print(f"  {'#'*lv} {t!r}")


def main():
    from docling.datamodel.base_models import InputFormat
    from docling.document_converter import DocumentConverter, PdfFormatOption

    opts = C._build_pdf_pipeline_options()
    converter = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
    )
    print("converting (Docling, CPU)...", flush=True)
    result = converter.convert(PDF)

    raw_md = result.document.export_to_markdown()
    dump("raw", raw_md)

    # add-on (with the suffix-match patch) + re-promotion, mirroring production
    try:
        from hierarchical.postprocessor import ResultPostprocessor
        try:
            C._patch_hierarchical_infer()
        except Exception as exc:
            print("patch failed:", exc)
        ResultPostprocessor(result, source=PDF).process()
    except Exception as exc:
        print("add-on failed:", exc)
    try:
        n = C._repromote_numbered_headings(result.document)
        print("re-promoted:", n)
    except Exception as exc:
        print("repromote failed:", exc)

    post_md = result.document.export_to_markdown()
    dump("postaddon", post_md)

    # Rank-1 over-prune guard
    post_headings = len(C._HEADING_RE.findall(post_md))
    raw_headings = len(C._HEADING_RE.findall(raw_md))
    md = post_md
    if post_headings < 3 <= raw_headings:
        print(f"\n[over-prune guard] {raw_headings}->{post_headings}: using raw_md")
        md = raw_md

    md = C._relevel_by_containment(C._relevel_headings(C.normalize_dashes(md)))
    dump("after_containment", md)
    if C._max_heading_level(md) < 2:
        md = C._relevel_by_numbering(md)
        dump("after_numbering_fallback", md)

    final_max = C._max_heading_level(md)
    print(f"\n>>> FINAL max_heading_level = {final_max}  (depth gate needs >=2)")


if __name__ == "__main__":
    main()
