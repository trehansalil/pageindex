"""Drive doc_store/ through the already-running MCP server + arq worker
(e.g. both attached to the VS Code debugger), instead of spawning isolated
converter subprocesses like preprocess_client.py does.

Usage:
    uv run python ingest_via_server.py [filename]
"""

import sys
import time
from pathlib import Path

import httpx
from dotenv import load_dotenv
import os

load_dotenv()

BASE_URL = f"http://localhost:{os.environ.get('MCP_PORT', '8201')}"
API_KEY = os.environ["UPLOAD_API_KEY"]
DOC_STORE = Path(__file__).parent / "doc_store"
SUPPORTED = {".pdf", ".docx", ".pptx", ".md", ".txt", ".html"}
POLL_INTERVAL_S = 3
POLL_TIMEOUT_S = 1800


def files_to_process(arg: str | None) -> list[Path]:
    if arg:
        path = DOC_STORE / arg
        if not path.exists():
            sys.exit(f"Error: {path} not found")
        return [path]
    return sorted(p for p in DOC_STORE.iterdir() if p.suffix.lower() in SUPPORTED)


def main() -> None:
    arg = sys.argv[1] if len(sys.argv) > 1 else None
    files = files_to_process(arg)
    if not files:
        sys.exit("No supported files found in doc_store/")

    print(f"Found {len(files)} file(s). Submitting to {BASE_URL}/upload/files ...\n")

    headers = {"X-API-Key": API_KEY}
    with httpx.Client(base_url=BASE_URL, headers=headers, timeout=120.0) as client:
        jobs = []
        for f in files:
            with open(f, "rb") as fh:
                resp = client.post("/upload/files", files=[("files", (f.name, fh))])
            if resp.status_code != 202:
                print(f"  [{f.name}] SUBMIT FAILED {resp.status_code}: {resp.text}")
                continue
            job = resp.json()[0]
            jobs.append((f.name, job["job_id"]))
            print(f"  [{f.name}] enqueued job {job['job_id']}")

        print("\nPolling job status (worker must be running)...\n")
        deadline = time.monotonic() + POLL_TIMEOUT_S
        pending = {job_id: name for name, job_id in jobs}
        while pending and time.monotonic() < deadline:
            for job_id in list(pending):
                name = pending[job_id]
                resp = client.get(f"/upload/status/{job_id}")
                if resp.status_code != 200:
                    continue
                data = resp.json()
                status = data.get("status")
                if status in ("done", "error"):
                    doc_id = data.get("doc_id")
                    content_class = data.get("content_class")
                    err = data.get("error")
                    if status == "done":
                        print(f"  [{name}] DONE doc_id={doc_id} class={content_class}")
                    else:
                        print(f"  [{name}] ERROR: {err}")
                    del pending[job_id]
            if pending:
                time.sleep(POLL_INTERVAL_S)

        for job_id, name in pending.items():
            print(f"  [{name}] TIMED OUT waiting for job {job_id}")


if __name__ == "__main__":
    main()
