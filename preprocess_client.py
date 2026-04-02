"""
FastMCP client — preprocess files from the doc_store folder.

Usage:
    python preprocess_client.py [filename] [--bg]

    filename  — name of a file inside doc_store/ (e.g. HR_FAQ.docx)
                If omitted, all supported files in doc_store/ are processed.
    --bg      — detach and run as a background process; output goes to preprocess.log

Supported extensions: .pdf  .docx  .pptx  .md  .txt

Hash cache is stored in MinIO at hashes/processed_hashes.json so it persists
across machines and is consistent with the rest of the document store.
"""

import asyncio
import base64
import hashlib
import json
import os
import subprocess
import sys
from io import BytesIO
from pathlib import Path

from dotenv import load_dotenv
from fastmcp import Client
from minio import Minio
from minio.error import S3Error

load_dotenv()

SERVER_URL = "http://localhost:8201/mcp"
DOC_STORE  = Path(__file__).parent / "doc_store"
SUPPORTED  = {".pdf", ".docx", ".pptx", ".md", ".txt"}
LOG_FILE   = Path(__file__).parent / "preprocess.log"

MINIO_ENDPOINT   = os.environ.get("MINIO_ENDPOINT",   "10.43.246.106:9000")
MINIO_ACCESS_KEY = os.environ.get("MINIO_ACCESS_KEY", "minioadmin")
MINIO_SECRET_KEY = os.environ.get("MINIO_SECRET_KEY", "minioadmin")
MINIO_BUCKET     = os.environ.get("MINIO_BUCKET",     "pageindex")
MINIO_SECURE     = os.environ.get("MINIO_SECURE",     "false").lower() == "true"
HASH_OBJECT      = "hashes/processed_hashes.json"

_mc: Minio | None = None


def _minio() -> Minio:
    global _mc
    if _mc is None:
        _mc = Minio(MINIO_ENDPOINT, access_key=MINIO_ACCESS_KEY,
                    secret_key=MINIO_SECRET_KEY, secure=MINIO_SECURE)
        if not _mc.bucket_exists(MINIO_BUCKET):
            _mc.make_bucket(MINIO_BUCKET)
    return _mc


def _load_hash_cache() -> dict[str, str]:
    try:
        response = _minio().get_object(MINIO_BUCKET, HASH_OBJECT)
        return json.loads(response.read())
    except S3Error as e:
        if e.code == "NoSuchKey":
            return {}
        raise
    finally:
        try:
            response.close(); response.release_conn()
        except Exception:
            pass


def _save_hash_cache(cache: dict[str, str]) -> None:
    data = json.dumps(cache, indent=2).encode()
    _minio().put_object(MINIO_BUCKET, HASH_OBJECT, BytesIO(data), len(data),
                        content_type="application/json")


def _sha256(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def _files_to_process(arg: str | None) -> list[Path]:
    if arg:
        path = DOC_STORE / arg
        if not path.exists():
            sys.exit(f"Error: {path} not found")
        if path.suffix.lower() not in SUPPORTED:
            sys.exit(f"Error: unsupported extension '{path.suffix}'. "
                     f"Supported: {', '.join(sorted(SUPPORTED))}")
        return [path]
    return sorted(p for p in DOC_STORE.iterdir() if p.suffix.lower() in SUPPORTED)


async def _process_one(
    client: Client,
    file: Path,
    file_hash: str,
    cache: dict[str, str],
    cache_lock: asyncio.Lock,
) -> None:
    content_b64 = base64.b64encode(file.read_bytes()).decode()

    result = await client.call_tool(
        "upload_and_process_document",
        {"filename": file.name, "content_base64": content_b64},
    )

    raw = result.content[0].text if result.content else "{}"
    data = json.loads(raw)

    if "error" in data:
        print(f"  [{file.name}] ERROR: {data['error']}", flush=True)
    else:
        print(f"  [{file.name}] doc_id: {data.get('doc_id')} — {data.get('message')}", flush=True)
        async with cache_lock:
            cache[file.name] = file_hash
            _save_hash_cache(cache)


async def preprocess(files: list[Path]) -> None:
    print("Loading hash cache from MinIO...", flush=True)
    cache = _load_hash_cache()
    cache_lock = asyncio.Lock()

    to_run: list[tuple[Path, str]] = []
    for file in files:
        h = _sha256(file)
        if cache.get(file.name) == h:
            print(f"Skipping (unchanged): {file.name}", flush=True)
        else:
            to_run.append((file, h))

    if not to_run:
        print("Nothing to process.")
        return

    print(f"Processing {len(to_run)} file(s) in parallel...", flush=True)
    client = Client(SERVER_URL)
    async with client:
        await asyncio.gather(*(
            _process_one(client, f, h, cache, cache_lock)
            for f, h in to_run
        ))


if __name__ == "__main__":
    args = sys.argv[1:]
    background = "--bg" in args
    if background:
        args.remove("--bg")

    arg = args[0] if args else None
    files = _files_to_process(arg)

    if not files:
        sys.exit("No supported files found in doc_store/")

    if background:
        log = open(LOG_FILE, "w")
        proc = subprocess.Popen(
            [sys.executable, __file__] + ([arg] if arg else []),
            stdout=log, stderr=log,
            start_new_session=True,
        )
        print(f"Background process started (PID {proc.pid}). Logging to {LOG_FILE}")
        sys.exit(0)

    print(f"Found {len(files)} file(s):")
    for f in files:
        print(f"  {f.name}")
    print()

    asyncio.run(preprocess(files))
