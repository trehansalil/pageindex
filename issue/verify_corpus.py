"""No-LLM corpus verifier for the depth<2 fix.

Loads Docling ONCE, runs the production pdf_to_markdown_docling() on each PDF, builds
the SAME structural tree md_to_tree() would (heading-level stack, no LLM summaries),
and runs the real validate_tree() gate. Reports node_count / depth / max_heading_level
/ gate verdict per file so we can confirm the fix on Cat B without regressing Cat A.

Usage:  ./.venv/bin/python issue/verify_corpus.py [substring ...]
"""
import os
import re
import sys
import time

sys.path.insert(0, os.path.join(os.path.dirname(__file__), "..", "src"))

from pageindex_mcp import converters as C  # noqa: E402
from pageindex_mcp.helpers import (  # noqa: E402
    validate_tree, _tree_node_count, _tree_depth,
)

DATA = os.path.join(os.path.dirname(__file__), "data")

# Mirror pageindex.page_index_md.extract_nodes_from_markdown + build_tree_from_nodes
_HDR = re.compile(r"^(#{1,6})\s+(.+)$")
_FENCE = re.compile(r"^```")


def md_to_struct_no_llm(md: str) -> list:
    """Build the structural tree md_to_tree would, minus LLM summaries/text."""
    nodes = []
    in_code = False
    for line in md.split("\n"):
        s = line.strip()
        if _FENCE.match(s):
            in_code = not in_code
            continue
        if not s or in_code:
            continue
        m = _HDR.match(s)
        if m:
            nodes.append({"level": len(m.group(1)), "title": m.group(2).strip()})
    # build_tree_from_nodes: level-stack nesting
    stack = []
    roots = []
    for n in nodes:
        lvl = n["level"]
        tn = {"title": n["title"], "text": "", "nodes": []}
        while stack and stack[-1][1] >= lvl:
            stack.pop()
        (roots if not stack else stack[-1][0]["nodes"]).append(tn)
        stack.append((tn, lvl))
    return roots


# Category labels for quick reading (from the outline survey).
CATS = {
    "AKB": "A", "AVB-PHV": "A", "Haftpflicht-Besondere": "A",
    "Katzen-Kranken": "B", "Katzen-OP": "B", "Hunde-Kranken": "B", "Hunde-OP": "B",
    "Pferde-Kranken": "B", "Pferde-OP": "B", "Meutenversicherung": "B",
    "Hundehalterhaftpflicht": "B", "Pferdehalterhaftpflicht": "B",
    "Haftpflicht-Allgemeine": "C", "Hundeleben-Allgemeine": "C",
    "Kundeninformation": "C", "Tier-OP-Kranken-Allgemeine": "C",
}


def cat_of(name: str) -> str:
    for k, v in CATS.items():
        if name.startswith(k):
            return v
    return "D"


def main():
    pats = sys.argv[1:]
    files = sorted(f for f in os.listdir(DATA) if f.lower().endswith(".pdf"))
    if pats:
        files = [f for f in files if any(p.lower() in f.lower() for p in pats)]
    print(f"verifying {len(files)} file(s)\n")
    rows = []
    for f in files:
        path = os.path.join(DATA, f)
        t0 = time.monotonic()
        try:
            md = C.pdf_to_markdown_docling(path)
            struct = md_to_struct_no_llm(md)
            ok, reason = validate_tree(struct)
            rows.append((cat_of(f), f, ok, reason or "PASS",
                         _tree_node_count(struct), _tree_depth(struct),
                         C._max_heading_level(md), time.monotonic() - t0))
        except Exception as e:
            rows.append((cat_of(f), f, False, f"ERR:{type(e).__name__}:{e}",
                         0, 0, 0, time.monotonic() - t0))
    rows.sort(key=lambda r: (r[0], r[1]))
    print(f"{'cat':3} {'ok':3} {'reason':14} {'nodes':>6} {'depth':>6} {'maxH':>5} {'sec':>6}  file")
    for cat, f, ok, reason, nc, dp, mh, sec in rows:
        print(f"{cat:3} {'Y' if ok else 'N':3} {reason[:14]:14} {nc:6} {dp:6} {mh:5} {sec:6.1f}  {f}")
    npass = sum(1 for r in rows if r[2])
    print(f"\n{npass}/{len(rows)} pass the gate")


if __name__ == "__main__":
    main()
