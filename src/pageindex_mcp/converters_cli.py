"""CLI entry point for subprocess-isolated document conversion.

Usage:
    python -m pageindex_mcp.converters_cli <input_pdf_path>

Stdout: exactly one JSON line at exit.
  success: {"ok": true, "doc_id": "...", "peak_rss_kib": <int>, "duration_ms": <int>}
  failure: {"ok": false, "error": "<ExceptionClassName>", "message": "..."}

Exit code: 0 on success, 1 on handled exception, signal-default on crash.

All logging goes to stderr. Stdout is reserved exclusively for the final JSON line.
Any stray print() calls from imported libraries are redirected to stderr so they
cannot pollute the single-JSON-line stdout contract.
"""

import argparse
import asyncio
import json
import logging
import resource
import sys
import time

# Redirect all logging to stderr immediately — before any other import.
logging.basicConfig(level=logging.INFO, stream=sys.stderr)

# _stdout is the stream used for the final JSON output line.
# It is a module-level variable so tests can monkeypatch it to a StringIO.
_stdout = sys.stdout


def _emit(payload: dict) -> None:
    """Write exactly one JSON line to _stdout and flush."""
    print(json.dumps(payload), file=_stdout, flush=True)


def _peak_rss_kib() -> int:
    """Return the peak RSS of this process in KiB.

    Linux ``ru_maxrss`` is reported in KiB; macOS reports it in bytes.
    """
    raw = resource.getrusage(resource.RUSAGE_SELF).ru_maxrss
    if sys.platform == "darwin":
        return raw // 1024
    return raw


async def main() -> int:
    """Run the CLI. Returns exit code (0 = success, 1 = failure)."""
    # Redirect sys.stdout to stderr BEFORE argparse so any usage/help/error
    # output from argparse (and any stray print() calls from libraries imported
    # below — Docling progress bars, litellm debug output) cannot pollute the
    # single-line JSON contract on stdout. _emit() writes to the saved
    # ``_stdout`` reference, bypassing this redirect. Restored in finally so
    # in-process callers (tests) don't leak the global stdout change.
    orig_stdout = sys.stdout
    sys.stdout = sys.stderr

    start = time.monotonic()

    try:
        parser = argparse.ArgumentParser(
            prog="converters_cli",
            description="Index a document via CustomPageIndexClient and emit JSON to stdout.",
        )
        parser.add_argument("input_path", help="Path to the input PDF (or other supported format).")
        try:
            args = parser.parse_args()
        except SystemExit as sysexit:
            # argparse calls sys.exit() on --help (code 0) or bad args (code 2).
            # Both are "handled failure" from the worker's perspective: the
            # documented CLI exit contract is 0 on success or 1 on handled
            # failure, so we coerce any argparse exit to 1. Emit a JSON line
            # first so the stdout-is-exactly-one-JSON-line contract holds.
            _emit(
                {
                    "ok": False,
                    "error": "ArgparseExit",
                    "message": f"argparse exited with code {sysexit.code}",
                }
            )
            return 1

        try:
            # Heavy import deferred to here so baseline RSS in the parent process
            # (before any conversion) is not polluted by pageindex/litellm imports.
            from pageindex_mcp.client import (
                CustomPageIndexClient,
                configure_litellm,
                validate_llm_config,
            )

            # Provider abstraction: validate the LLM config and point the fork's
            # litellm calls at the configured (OpenAI-compatible / Azure) endpoint
            # before indexing — the ingestion path no longer relies on litellm
            # reading OPENAI_BASE_URL from the environment by chance.
            validate_llm_config()
            configure_litellm()

            client = CustomPageIndexClient()
            doc_id = await client.index(args.input_path)

            duration_ms = int((time.monotonic() - start) * 1000)
            payload = {
                "ok": True,
                "doc_id": doc_id,
                "peak_rss_kib": _peak_rss_kib(),
                "duration_ms": duration_ms,
            }
            # RFC-004 Amendment 1 (Step 5 integration): when index() routed the
            # doc to the flat success path it stamps last_content_class. Surface it
            # in the stdout JSON so the worker hash carries content_class
            # (FLAT-04-C1). Absent for a normal tree doc.
            content_class = getattr(client, "last_content_class", None)
            if content_class:
                payload["content_class"] = content_class
            _emit(payload)
            return 0

        except Exception as exc:
            duration_ms = int((time.monotonic() - start) * 1000)
            payload = {
                "ok": False,
                "error": type(exc).__name__,
                "message": str(exc),
            }
            _emit(payload)
            logging.getLogger(__name__).exception("converters_cli failed: %s", exc)
            return 1
    finally:
        # LLM-02: this is a short-lived subprocess — flush buffered spans before
        # exit or they are lost. Two distinct providers must be flushed: the
        # langfuse-python client (any query-path spans) AND litellm's private
        # langfuse_otel OTel provider (the ingestion-path generations + cost).
        # Both are no-ops when tracing is disabled. Deferred imports so the parent
        # baseline RSS is clean.
        try:
            from pageindex_mcp.client import flush_litellm_tracing
            from pageindex_mcp.tracing import flush_langfuse

            flush_langfuse()
            flush_litellm_tracing()
        except Exception:  # pragma: no cover - never let flush break the CLI contract
            logging.getLogger(__name__).debug("Langfuse flush skipped", exc_info=True)
        sys.stdout = orig_stdout


if __name__ == "__main__":
    sys.exit(asyncio.run(main()))
