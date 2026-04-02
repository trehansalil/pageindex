"""
Upload and process PDF documents into the local PageIndex MCP server.

Usage:
    # Single file or URL
    python upload.py /path/to/document.pdf
    python upload.py https://example.com/report.pdf

    # All PDFs in a folder (parallel)
    python upload.py /path/to/folder/

    # Limit parallel workers (default: 4)
    python upload.py /path/to/folder/ --workers 2
"""

import asyncio
import sys
import json
import argparse
from pathlib import Path
from langchain_mcp_adapters.client import MultiServerMCPClient

MCP_URL = "http://localhost:8201/mcp"


async def process_one(semaphore: asyncio.Semaphore, tools: dict, source: str) -> dict:
    async with semaphore:
        print(f"[START] {source}")
        try:
            result = await tools["process_document"].ainvoke({"url": source})
            data = json.loads(result)
            if "error" in data:
                print(f"[FAIL]  {source}\n        {data['error']}")
                return {"source": source, "status": "error", "error": data["error"]}
            print(f"[DONE]  {source}  →  doc_id: {data['doc_id']}")
            return {"source": source, "status": "ok", "doc_id": data["doc_id"], "filename": data["filename"]}
        except Exception as e:
            print(f"[FAIL]  {source}\n        {e}")
            return {"source": source, "status": "error", "error": str(e)}


async def run(sources: list[str], workers: int):
    client = MultiServerMCPClient({
        "pageindex": {"transport": "streamable_http", "url": MCP_URL}
    })
    tools = {t.name: t for t in await client.get_tools()}

    semaphore = asyncio.Semaphore(workers)
    tasks = [process_one(semaphore, tools, s) for s in sources]

    print(f"Processing {len(sources)} file(s) with up to {workers} parallel worker(s)...\n")
    results = await asyncio.gather(*tasks)

    ok = [r for r in results if r["status"] == "ok"]
    failed = [r for r in results if r["status"] == "error"]

    print(f"\n{'='*50}")
    print(f"Completed: {len(ok)}/{len(results)}  |  Failed: {len(failed)}")
    if ok:
        print("\nProcessed documents:")
        for r in ok:
            print(f"  {r['doc_id']}  {r['filename']}")
    if failed:
        print("\nFailed:")
        for r in failed:
            print(f"  {r['source']}: {r['error']}")


def collect_sources(target: str) -> list[str]:
    path = Path(target)
    if path.is_dir():
        pdfs = sorted(path.glob("*.pdf"))
        if not pdfs:
            print(f"No PDF files found in {target}")
            sys.exit(1)
        return [str(p) for p in pdfs]
    return [target]


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Upload PDFs to PageIndex MCP server")
    parser.add_argument("source", help="PDF file, URL, or folder containing PDFs")
    parser.add_argument("--workers", type=int, default=4, help="Max parallel jobs (default: 4)")
    args = parser.parse_args()

    sources = collect_sources(args.source)
    asyncio.run(run(sources, args.workers))
