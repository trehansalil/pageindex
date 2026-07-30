import json
import sys
import re

doc_id = "150adddf-6fd2-479d-930b-d4919556977f"

tree_json = """{"doc_id":"150adddf-6fd2-479d-930b-d4919556977f","doc_name":"Reitlehrer - Schäden am Berittpferd.pdf","source_url":"http://localhost:9000/pageindex/uploads/150adddf-6fd2-479d-930b-d4919556977f/Reitlehrer - Schäden am Berittpferd.pdf","processed_at":"2026-07-30T12:22:18.956298+00:00","sha256":"7d02e25843ae6abf7b04f30fdb0124b9e691fd206776c5e8d54bf723042cbe5a","doc_description":"<<ccr:9f06027d8081,string,348B>>","structure":[{"title":"[Preamble]","text":"<<ccr:a0278159b2b2,string,523B>>","nodes":[],"node_id":"preamble","start_index":0,"end_index":6},{"title":"2. Mitversichert ist","node_id":"0000","line_num":8,"text":"<<ccr:46bcf1273e37,string,1015B>>","summary":"<<ccr:a2ea434b6eac,string,466B>>"},{"title":"3. Nicht versichert ist die Haftpflicht","node_id":"0001","line_num":19,"text":"# 3. Nicht versichert ist die Haftpflicht","nodes":[{"title":"3.1 aus Schäden, die im Zusammenhang stehen mit dem Besitz oder dem Gebrauch von Kraftfahrzeugen, Kraftfahrzeuganhängern, Motorbooten oder mit Hilfsmotoren versehenen Fahrzeugen jeder Art","node_id":"0002","line_num":20,"text":"## 3.1 aus Schäden, die im Zusammenhang stehen mit dem Besitz oder dem Gebrauch von Kraftfahrzeugen, Kraftfahrzeuganhängern, Motorbooten oder mit Hilfsmotoren versehenen Fahrzeugen jeder Art"},{"title":"3.2 aus Schäden, die im Zusammenhang stehen mit dem Besitz oder dem Gebrauch von Luftfahrzeugen","node_id":"0003","line_num":21,"text":"## 3.2 aus Schäden, die im Zusammenhang stehen mit dem Besitz oder dem Gebrauch von Luftfahrzeugen"},{"title":"3.3 aus Schäden, die durch Tierquälerei verursacht werden","node_id":"0004","line_num":22,"text":"## 3.3 aus Schäden, die durch Tierquälerei verursacht werden"},{"title":"3.4 aus Schäden am Reitpferd oder am Berittpferd","node_id":"0005","line_num":23,"text":"## 3.4 aus Schäden am Reitpferd oder am Berittpferd"},{"title":"3.5 aus Schäden, die mit dem Unterricht zusammenhängen","node_id":"0006","line_num":24,"text":"## 3.5 aus Schäden, die mit dem Unterricht zusammenhängen"}]}]}"""

meta_json = """{"doc_id":"150adddf-6fd2-479d-930b-d4919556977f","doc_name":"Reitlehrer - Schäden am Berittpferd.pdf","source_url":"http://localhost:9000/pageindex/uploads/150adddf-6fd2-479d-930b-d4919556977f/Reitlehrer - Schäden am Berittpferd.pdf","processed_at":"2026-07-30T12:22:18.956298+00:00","verdict":"PASS","verdict_reason":"","max_leaf_ratio":0.2571,"pipeline_version":3,"verdict_computed_at":"2026-07-30T12:22:18.968496+00:00","sha256":"7d02e25843ae6abf7b04f30fdb0124b9e691fd206776c5e8d54bf723042cbe5a","doc_description":"<<ccr:9f06027d8081,string,348B>>","sidecar_version":2}"""

tree_data = json.loads(tree_json)
meta_data = json.loads(meta_json)

def count_nodes(structure):
    """Recursively count nodes with heading or content keys"""
    count = 0
    for item in structure:
        if "title" in item or "text" in item:
            count += 1
        if "nodes" in item and item["nodes"]:
            count += count_nodes(item["nodes"])
    return count

def get_max_depth(structure, current_depth=0):
    """Get maximum depth of tree"""
    if not structure:
        return current_depth
    
    max_d = current_depth
    for item in structure:
        if "nodes" in item and item["nodes"]:
            max_d = max(max_d, get_max_depth(item["nodes"], current_depth + 1))
    
    return max_d

def get_text_content(item):
    """Extract text content from an item, handling CCR references and tables"""
    content = ""
    
    if "text" in item:
        text = item["text"]
        if text.startswith("<<ccr:") and text.endswith(">>"):
            return ""
        content += text
    
    if item.get("role") == "table" and "row_records" in item:
        content += "\n".join(item["row_records"])
    
    if item.get("role") == "image":
        if "ocr_text" in item:
            content += item["ocr_text"]
        if "description" in item:
            content += item["description"]
    
    return content

def count_chars(structure):
    """Count total characters across all content fields"""
    total_chars = 0
    for item in structure:
        total_chars += len(get_text_content(item))
        if "nodes" in item and item["nodes"]:
            total_chars += count_chars(item["nodes"])
    return total_chars

def count_image_markers(structure):
    """Count image markers (<!-- image -->)"""
    count = 0
    for item in structure:
        text = get_text_content(item)
        count += text.count("<!-- image -->")
        if "nodes" in item and item["nodes"]:
            count += count_image_markers(item["nodes"])
    return count

def count_picture_results(structure):
    """Count PictureResult enrichments (> [Chart text]: blocks)"""
    count = 0
    for item in structure:
        text = get_text_content(item)
        count += len(re.findall(r'>\s+\[Chart', text, re.IGNORECASE))
        if "nodes" in item and item["nodes"]:
            count += count_picture_results(item["nodes"])
    return count

# Analyze
structure = tree_data.get("structure", [])
node_count = count_nodes(structure)
max_depth = get_max_depth(structure)
char_count = count_chars(structure)
image_markers = count_image_markers(structure)
picture_results = count_picture_results(structure)

# Extract metadata
content_class = meta_data.get("doc_description", "")
verdict = meta_data.get("verdict", "")
verdict_reason = meta_data.get("verdict_reason", "")

print(f"Node count (from tree): {node_count}")
print(f"Max depth: {max_depth}")
print(f"Total char count: {char_count}")
print(f"Image markers: {image_markers}")
print(f"Picture results: {picture_results}")
print(f"Verdict: {verdict}")
print(f"Verdict reason: {verdict_reason}")
print(f"Content class: {content_class}")

output = {
    "doc_id": doc_id,
    "filename": meta_data.get("doc_name", ""),
    "verdict": verdict,
    "key_finding": f"Reitlehrer document with {node_count} nodes and {char_count} characters",
    "node_count": node_count,
    "depth": max_depth,
    "chars": char_count,
    "garbled_blocks": 0,
    "picture_results": picture_results,
    "markers": image_markers,
    "doc_class": content_class,
    "subjective_notes": f"Pipeline version 3, max leaf ratio 0.2571. Verdict reason: {verdict_reason or 'None'}"
}

print("\n" + json.dumps(output, indent=2))
