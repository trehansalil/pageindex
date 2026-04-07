"""
Preprocess files from the doc_store folder using CustomPageIndexClient directly.

Usage:
    python preprocess_client.py [filename] [--bg]

    filename  — name of a file inside doc_store/ (e.g. HR_FAQ.docx)
                If omitted, all supported files in doc_store/ are processed.
    --bg      — detach and run as a background process; output goes to preprocess.log

Supported extensions: .pdf  .docx  .pptx  .md  .txt  .html

Hash-based deduplication is handled inside CustomPageIndexClient.index() — unchanged
files are skipped automatically. The cache is stored in MinIO at
hashes/processed_hashes.json and is shared with the rest of the document store.
"""


import asyncio
import subprocess
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# Suppress litellm LoggingWorker shutdown noise.
# These tracebacks are written directly to stderr by asyncio internals and
# bypass the loop exception handler, so we filter at the stream level.
# The filter is *stateful*: once a "trigger" line is seen, the entire
# traceback block (indented frames, chained-exception headers, etc.) is
# suppressed until a clearly non-traceback line appears.
# ---------------------------------------------------------------------------
_NOISE_TRIGGERS = (
    "Task was destroyed but it is pending",
    "Task exception was never retrieved",
    "unhandled exception during asyncio.run() shutdown",
    "future: <Task finished",
    "task_done() called too many times",
    "cannot reuse already awaited coroutine",
    "LoggingWorker",
    "logging_worker.py",
    "litellm_logging.py",
)


class _FilteredStderr:
    """Stateful stderr filter that drops entire litellm traceback blocks."""

    def __init__(self, wrapped):
        self._wrapped = wrapped
        self._buf = ""
        self._suppressing = False

    def _is_traceback_continuation(self, line: str) -> bool:
        """Return True if *line* looks like part of an ongoing traceback."""
        s = line.strip()
        return (
            not s
            or line[0] in (" ", "\t")
            or s.startswith("Traceback")
            or s.startswith("File ")
            or s.startswith("During handling")
            or s.startswith("The above exception")
            or s.startswith("asyncio.exceptions.")
            or s.startswith("ValueError:")
            or s.startswith("RuntimeError:")
            or s.startswith("future:")
            or s.startswith("task:")
            or all(c in "^ " for c in s)
        )

    def write(self, text: str) -> int:
        self._buf += text
        lines = self._buf.split("\n")
        self._buf = lines[-1]  # hold incomplete last line
        for line in lines[:-1]:
            # Trigger: enter suppression mode
            if any(t in line for t in _NOISE_TRIGGERS):
                self._suppressing = True
                continue
            if self._suppressing:
                if self._is_traceback_continuation(line):
                    continue
                # Non-traceback line — stop suppressing and emit it
                self._suppressing = False
            self._wrapped.write(line + "\n")
        return len(text)

    def flush(self) -> None:
        if self._buf and not self._suppressing:
            self._wrapped.write(self._buf)
        self._buf = ""
        self._wrapped.flush()

    def __getattr__(self, name):
        return getattr(self._wrapped, name)

from dotenv import load_dotenv

load_dotenv()

DOC_STORE = Path(__file__).parent / "doc_store"
SUPPORTED = {".pdf", ".docx", ".pptx", ".md", ".txt", ".html"}
LOG_FILE  = Path(__file__).parent / "preprocess.log"


def _files_to_process(arg: str | None) -> list[Path]:
    if arg:
        path = DOC_STORE / arg
        if not path.exists():
            sys.exit(f"Error: {path} not found")
        if path.suffix.lower() not in SUPPORTED:
            sys.exit(
                f"Error: unsupported extension '{path.suffix}'. "
                f"Supported: {', '.join(sorted(SUPPORTED))}"
            )
        return [path]
    return sorted(p for p in DOC_STORE.iterdir() if p.suffix.lower() in SUPPORTED)


async def _process_one(client, file: Path) -> None:
    try:
        doc_id = await client.index(str(file))
        print(f"  [{file.name}] doc_id: {doc_id}", flush=True)
    except Exception as e:
        print(f"  [{file.name}] ERROR: {e}", flush=True)


async def preprocess(files: list[Path]) -> None:
    from pageindex_mcp.client import CustomPageIndexClient

    print(f"Processing {len(files)} file(s) in parallel...", flush=True)
    client = CustomPageIndexClient()
    await asyncio.gather(*(_process_one(client, f) for f in files))


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
            stdout=log,
            stderr=log,
            start_new_session=True,
        )
        print(f"Background process started (PID {proc.pid}). Logging to {LOG_FILE}")
        sys.exit(0)

    print(f"Found {len(files)} file(s):")
    for f in files:
        print(f"  {f.name}")
    print()

    # Install stderr filter before running so litellm LoggingWorker shutdown
    # noise is suppressed regardless of whether it comes through asyncio's
    # exception handler or is written directly to stderr by the runtime.
    sys.stderr = _FilteredStderr(sys.stderr)
    try:
        with asyncio.Runner() as runner:
            loop = runner.get_loop()
            _orig = loop.call_exception_handler

            def _exception_handler(ctx: dict) -> None:
                exc = ctx.get("exception")
                msg = ctx.get("message", "")
                task = ctx.get("task")
                if (
                    any(s in msg for s in _NOISE_TRIGGERS)
                    or any(s in repr(task) for s in _NOISE_TRIGGERS)
                    or (isinstance(exc, (ValueError, RuntimeError))
                        and any(s in str(exc) for s in _NOISE_TRIGGERS))
                ):
                    return
                _orig(ctx)

            loop.set_exception_handler(_exception_handler)
            runner.run(preprocess(files))
    finally:
        sys.stderr = sys.stderr._wrapped  # type: ignore[union-attr]
