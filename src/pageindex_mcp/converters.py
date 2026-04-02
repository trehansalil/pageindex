"""Document format conversion helpers and tree search utilities."""


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
