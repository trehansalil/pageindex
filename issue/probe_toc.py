"""Probe the non-numbering hierarchy signals available for Katzen-Kranken:
   (1) the PyMuPDF PDF outline/bookmarks, (2) Docling SectionHeaderItem.level,
   (3) the hierarchical add-on's _extract_toc() heading_to_level.
"""
import os
import sys

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

PDF = os.path.join(
    os.path.dirname(__file__), "data",
    "Katzen-Kranken-Besondere-Bedingungen-2024-002.pdf.pdf",
)


def probe_pymupdf_outline():
    import pymupdf
    doc = pymupdf.open(PDF)
    toc = doc.get_toc(simple=False)
    print(f"\n===== PyMuPDF get_toc(): {len(toc)} entries")
    for entry in toc:
        lvl, title, page = entry[0], entry[1], entry[2]
        print(f"  L{lvl} p{page}: {title!r}")
    doc.close()


def probe_docling_levels(result):
    from docling_core.types.doc.document import SectionHeaderItem
    print("\n===== Docling SectionHeaderItem.level (raw):")
    for item, _ in result.document.iterate_items(with_groups=False):
        if isinstance(item, SectionHeaderItem):
            lvl = getattr(item, "level", None)
            print(f"  level={lvl}: {(item.text or '')[:70]!r}")


def probe_addon_toc(result):
    from hierarchical.hierarchy_builder_metadata import HierarchyBuilderMetadata
    try:
        hbm = HierarchyBuilderMetadata(result, raise_on_error=False)
    except TypeError:
        # constructor signature may differ; try positional conv_res
        hbm = HierarchyBuilderMetadata(conv_res=result, raise_on_error=False)
    try:
        heading_to_level = hbm._extract_toc()
        print(f"\n===== add-on _extract_toc(): {len(heading_to_level)} entries")
        for level, title, page, add_info in heading_to_level:
            print(f"  L{level} p{page}: {title!r}")
    except Exception as exc:
        print("add-on _extract_toc failed:", repr(exc))


def main():
    probe_pymupdf_outline()

    from docling.datamodel.base_models import InputFormat
    from docling.document_converter import DocumentConverter, PdfFormatOption
    from pageindex_mcp import converters as C

    opts = C._build_pdf_pipeline_options()
    converter = DocumentConverter(
        format_options={InputFormat.PDF: PdfFormatOption(pipeline_options=opts)}
    )
    print("\nconverting (Docling, CPU)...", flush=True)
    result = converter.convert(PDF)
    probe_docling_levels(result)
    probe_addon_toc(result)


if __name__ == "__main__":
    main()
