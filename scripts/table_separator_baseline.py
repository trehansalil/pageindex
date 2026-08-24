#!/usr/bin/env python3
"""Pre-redeploy table-separator baseline probe (RFC-034 D2.5, read-only).

Enumerates processed/*.json trees whose processed_at (from the sibling
.meta.json) falls in the 2026-07-30..2026-08-04 window and counts
`|----| ` (unrepaired GFM) vs `| --- |` (repaired, RFC-005 _repair_docling_tables)
table separator lines per doc. No MinIO writes.

Usage:
  python scripts/table_separator_baseline.py
"""

import json
import os
import re
import sys
from datetime import datetime

from minio import Minio

WINDOW_START = "2026-07-30"
WINDOW_END = "2026-08-04"
UNREPAIRED_RE = re.compile(r"\|-{3,}\| ")
REPAIRED_RE = re.compile(r"\| --- \|")

OUT_PATH = "audit/TABLE_SEPARATOR_BASELINE_2026-08-08.md"


def client():
    return Minio(
        os.getenv("MINIO_ENDPOINT", "localhost:9000"),
        access_key=os.getenv("MINIO_ACCESS_KEY", "minioadmin"),
        secret_key=os.getenv("MINIO_SECRET_KEY", "minioadmin"),
        secure=os.getenv("MINIO_SECURE", "false").lower() == "true",
    )


def in_window(processed_at):
    if not processed_at:
        return False
    try:
        day = datetime.fromisoformat(processed_at.replace("Z", "+00:00")).date().isoformat()
    except ValueError:
        return False
    return WINDOW_START <= day <= WINDOW_END


def main():
    c = client()
    bucket = os.getenv("MINIO_BUCKET", "pageindex")
    all_objects = {
        o.object_name for o in c.list_objects(bucket, prefix="processed/", recursive=True)
    }
    meta_files = sorted(o for o in all_objects if o.endswith(".meta.json"))

    rows = []
    for mf in meta_files:
        doc_id = mf.replace("processed/", "").replace(".meta.json", "")
        tree_key = f"processed/{doc_id}.json"
        if tree_key not in all_objects:
            continue
        try:
            meta = json.loads(c.get_object(bucket, mf).read().decode())
        except Exception as e:
            print(f"skip {doc_id}: meta read failed: {e}", file=sys.stderr)
            continue
        processed_at = meta.get("processed_at", "")
        if not in_window(processed_at):
            continue
        tree_text = c.get_object(bucket, tree_key).read().decode()
        unrepaired = len(UNREPAIRED_RE.findall(tree_text))
        repaired = len(REPAIRED_RE.findall(tree_text))
        rows.append((doc_id, meta.get("doc_name", doc_id), processed_at, unrepaired, repaired))

    rows.sort(key=lambda r: r[2])

    lines = [
        "# Table-Separator Baseline (2026-08-08)",
        "",
        f"Pre-redeploy read-only probe, RFC-034 D2.5. Window: {WINDOW_START}..{WINDOW_END}.",
        "",
        "| doc_id | doc_name | processed_at | unrepaired `|----|` | repaired `| --- |` |",
        "|---|---|---|---|---|",
    ]
    for doc_id, doc_name, processed_at, unrepaired, repaired in rows:
        lines.append(f"| {doc_id} | {doc_name} | {processed_at} | {unrepaired} | {repaired} |")
    lines.append("")
    lines.append(f"Total docs in window: {len(rows)}")

    with open(OUT_PATH, "w") as f:
        f.write("\n".join(lines) + "\n")

    print(f"wrote {OUT_PATH}: {len(rows)} docs in window")


if __name__ == "__main__":
    main()
