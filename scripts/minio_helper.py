#!/usr/bin/env python3
"""MinIO helper for corpus quality cycle agents.

Usage:
  python scripts/minio_helper.py list              # list all processed meta files
  python scripts/minio_helper.py meta <doc_id>     # print meta.json for a doc
  python scripts/minio_helper.py tree <doc_id>     # print tree JSON (first 500 lines)
  python scripts/minio_helper.py inventory         # full inventory: doc_id, filename, has_tree
"""

import json
import os
import sys
import time

from minio import Minio
from minio.error import S3Error
from urllib3.exceptions import HTTPError

RETRY_DELAYS = (2, 4, 8)


def client():
    return Minio(
        os.getenv("MINIO_ENDPOINT", "localhost:9000"),
        access_key=os.getenv("MINIO_ACCESS_KEY", "minioadmin"),
        secret_key=os.getenv("MINIO_SECRET_KEY", "minioadmin"),
        secure=False,
    )


def get_object_with_retry(c, bucket, *keys):
    """Fetch the first available key, retrying transient read failures.

    Each attempt tries every key in order before backing off, so a document
    that only has the flat artifact does not pay the full backoff budget on
    the primary key before the fallback is considered. Non-transient S3
    errors propagate immediately; exhausted retries re-raise the last error
    after logging the attempt count (never silently swallowed).
    """
    attempts = (0, *RETRY_DELAYS)
    last_error = None
    for delay in attempts:
        if delay:
            time.sleep(delay)
        for key in keys:
            try:
                return c.get_object(bucket, key)
            except S3Error as e:
                if e.code != "NoSuchKey":
                    raise
                last_error = e
            except (HTTPError, OSError) as e:
                last_error = e
    print(
        f"MinIO read failed for {bucket}/{keys} after {len(attempts)} attempts: {last_error}",
        file=sys.stderr,
    )
    raise last_error


def cmd_list():
    c = client()
    for o in c.list_objects("pageindex", prefix="processed/", recursive=True):
        if o.object_name.endswith(".meta.json"):
            doc_id = o.object_name.replace("processed/", "").replace(".meta.json", "")
            print(doc_id)


def cmd_meta(doc_id):
    c = client()
    data = get_object_with_retry(c, "pageindex", f"processed/{doc_id}.meta.json")
    print(data.read().decode())


def cmd_tree(doc_id, max_lines=500):
    c = client()
    try:
        data = get_object_with_retry(
            c,
            "pageindex",
            f"processed/{doc_id}.json",
            f"processed/{doc_id}.flat.json",
        )
    except Exception as e:
        print(f"No tree or flat file found for {doc_id}: {e}", file=sys.stderr)
        return
    lines = data.read().decode().split("\n")[:max_lines]
    print("\n".join(lines))


def cmd_inventory():
    c = client()
    all_objects = {
        o.object_name for o in c.list_objects("pageindex", prefix="processed/", recursive=True)
    }
    meta_files = sorted(o for o in all_objects if o.endswith(".meta.json"))
    results = []
    for mf in meta_files:
        doc_id = mf.replace("processed/", "").replace(".meta.json", "")
        has_tree = f"processed/{doc_id}.json" in all_objects
        has_flat = f"processed/{doc_id}.flat.json" in all_objects
        try:
            meta_data = json.loads(c.get_object("pageindex", mf).read().decode())
            filename = meta_data.get("doc_name", meta_data.get("original_filename", doc_id))
        except Exception:
            filename = doc_id
        results.append(
            {
                "doc_id": doc_id,
                "filename": filename,
                "has_tree": has_tree,
                "has_flat": has_flat,
                "has_meta": True,
            }
        )
    print(json.dumps(results, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    if len(sys.argv) < 2:
        print(__doc__)
        sys.exit(1)
    cmd = sys.argv[1]
    if cmd == "list":
        cmd_list()
    elif cmd == "meta" and len(sys.argv) > 2:
        cmd_meta(sys.argv[2])
    elif cmd == "tree" and len(sys.argv) > 2:
        cmd_tree(sys.argv[2])
    elif cmd == "inventory":
        cmd_inventory()
    else:
        print(__doc__)
        sys.exit(1)
