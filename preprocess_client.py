"""
Preprocess files from the doc_store folder through the isolated converter subprocess.

Usage:
    python preprocess_client.py [filename] [--bg]

    filename  — name of a file inside doc_store/ (e.g. HR_FAQ.docx)
                If omitted, all supported files in doc_store/ are processed.
    --bg      — detach and run as a background process; output goes to preprocess.log

Supported extensions: see pageindex_mcp.client._SUPPORTED (.pdf .docx .pptx .md
.markdown .txt .html .xlsx .png .jpg .jpeg .tif .tiff)

Each file is indexed in a FRESH child process (``pageindex_mcp.converters_cli``,
the same isolation the arq worker uses via ``_run_converter_subprocess``). Docling
model weights, PyTorch caches, and glibc arenas — ~1.4 GB that torch never returns
to the OS — are reclaimed at child exit instead of accumulating in this long-lived
parent. Processing is SEQUENTIAL by default (``PREPROCESS_CONCURRENCY=1``), mirroring
the worker's ``MAX_JOBS=1`` so peak RSS stays bounded to a single child; raise the
env var only where the machine has RAM headroom (each child can peak ~1.7 GB).

Hash-based deduplication is handled inside CustomPageIndexClient.index() (run in the
child) — unchanged files are skipped automatically. The cache is stored in MinIO at
hashes/processed_hashes.json and is shared with the rest of the document store.
"""

import asyncio
import os
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

from pageindex_mcp.client import _SUPPORTED as SUPPORTED

load_dotenv()

DOC_STORE = Path(__file__).parent / "doc_store"
LOG_FILE = Path(__file__).parent / "preprocess.log"


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


def _concurrency() -> int:
    """Max converter children in flight. Default 1 — mirrors the worker's
    MAX_JOBS=1, bounding peak RSS to a single ~1.7 GB child. Raise via
    PREPROCESS_CONCURRENCY only where the machine has RAM headroom."""
    try:
        return max(1, int(os.getenv("PREPROCESS_CONCURRENCY", "1")))
    except ValueError:
        return 1


async def _process_one(sem: asyncio.Semaphore, file: Path) -> None:
    # Same isolation primitive the arq worker uses: a fresh converters_cli child
    # per file that dies (and frees Docling/torch memory) when it returns. The
    # child runs CustomPageIndexClient.index() in-process, then exits.
    from pageindex_mcp.worker import ConverterOOMError, _run_converter_subprocess

    async with sem:
        try:
            result = await _run_converter_subprocess(str(file))
        except ConverterOOMError:
            print(f"  [{file.name}] ERROR: converter child OOM-killed", flush=True)
            return
        except TimeoutError:
            print(f"  [{file.name}] ERROR: converter child timed out", flush=True)
            return
        except Exception as e:
            # Report and continue to the next file (matches prior behaviour).
            print(f"  [{file.name}] ERROR: {e}", flush=True)
            return

    doc_id = result.get("doc_id")
    content_class = result.get("content_class")
    peak_mb = (result.get("peak_rss_kib") or 0) / 1024
    cls = f" class={content_class}" if content_class else ""
    print(f"  [{file.name}] doc_id: {doc_id}{cls} (child peak {peak_mb:.0f} MB)", flush=True)

    if doc_id:
        from pageindex_mcp.worker import _upsert_registry_row

        try:
            await _upsert_registry_row(doc_id, content_class)
        except Exception as exc:
            print(f"  [{file.name}] registry upsert failed (non-fatal): {exc}", flush=True)


async def _init_registry_pool() -> None:
    """Open the Postgres registry pool (mirrors worker.startup)."""
    from pageindex_mcp.config import _load_settings

    settings = _load_settings()
    if not (settings.registry_enabled and settings.postgres_dsn):
        return
    from pageindex_mcp.registry import init_registry

    try:
        await init_registry(settings.postgres_dsn)
        print("  Registry pool initialised", flush=True)
    except Exception as exc:
        print(f"  Registry pool init failed (dual-write disabled): {exc}", flush=True)


async def _close_registry_pool() -> None:
    from pageindex_mcp.config import _load_settings

    settings = _load_settings()
    if not (settings.registry_enabled and settings.postgres_dsn):
        return
    from pageindex_mcp.registry import close_registry

    try:
        await close_registry()
    except Exception:
        pass


async def preprocess(files: list[Path]) -> None:
    concurrency = _concurrency()
    print(
        f"Processing {len(files)} file(s) via isolated converter subprocesses "
        f"(concurrency={concurrency})...",
        flush=True,
    )
    await _init_registry_pool()
    sem = asyncio.Semaphore(concurrency)
    try:
        await asyncio.gather(*(_process_one(sem, f) for f in files))
    finally:
        await _close_registry_pool()


async def recompute_verdicts(doc_id: str | None = None) -> None:
    """Recompute verdict for one or all docs without re-ingestion (RFC-014 D3)."""
    import json
    from datetime import UTC, datetime
    from pageindex_mcp.config import CURRENT_PIPELINE_VERSION, _load_settings
    from pageindex_mcp.helpers import (
        HARD_FAIL_DEFECTS,
        REASON_POLICY,
        TreeDefect,
        TreeGateResult,
        _defect_from_reason_str,
        _ReasonPolicy,
        _tree_max_leaf_ratio,
        classify_verdict,
        validate_tree,
    )
    from pageindex_mcp.storage import get_minio, save_doc_meta, write_verdict

    settings = _load_settings()
    mc = get_minio()

    if doc_id:
        doc_ids = [doc_id]
    else:
        # List all processed docs
        objects = mc.list_objects(settings.minio_bucket, prefix="processed/", recursive=True)
        doc_ids = []
        for obj in objects:
            name = obj.object_name or ""
            if name.endswith(".json") and not name.endswith(".meta.json"):
                did = name.replace("processed/", "")
                # Strip .flat.json or .json to get pure doc_id
                if did.endswith(".flat.json"):
                    did = did[: -len(".flat.json")]
                else:
                    did = did[: -len(".json")]
                doc_ids.append(did)

    print(f"Recomputing verdicts for {len(doc_ids)} doc(s)...", flush=True)
    updated = 0
    errors = 0

    for did in doc_ids:
        try:
            key = f"processed/{did}.json"
            try:
                response = mc.get_object(settings.minio_bucket, key)
            except Exception:
                key = f"processed/{did}.flat.json"
                response = mc.get_object(settings.minio_bucket, key)
            try:
                data = json.loads(response.read())
            finally:
                response.close()
                response.release_conn()

            content_class = data.get("content_class", "")
            # Finding 5 (audit 2026-07-21): a flat doc's persisted JSON has a
            # "blocks" list of role-typed dicts and NO "structure" key (see
            # save_flat_doc, client.py:773-791) — those blocks don't have the
            # "nodes"/"title"/"text" shape classify_verdict/_tree_max_leaf_ratio
            # expect, so walking them as a tree produced nonsense metrics and
            # persisted verdict drift. client.py computes a flat doc's verdict
            # exactly once, at ingest time, from a pre-flat-routing tree that is
            # never persisted (client.py:757-764) — that computation cannot be
            # reproduced here. Mirror ingest instead: a flat doc already carries
            # its own verdict/verdict_reason/max_leaf_ratio on `data`
            # (client.py:785-787), so reuse those verbatim rather than inventing
            # a new heuristic over the block list. Tree docs (which always carry
            # a "structure" key, even an empty one) keep the existing path.
            is_flat = "structure" not in data and "blocks" in data

            if is_flat:
                verdict = data.get("verdict", "")
                verdict_reason = data.get("verdict_reason", "")
                # Zone-1: reconcile the stored verdict against the CURRENT
                # defect policy via a reconstructed TreeGateResult rather
                # than raw-string branching.
                #
                # classify_verdict is deliberately NOT re-run here: a flat
                # doc has no "structure", and the ingest-time inputs that
                # produced its verdict (image_enrichment_ratio above all)
                # are not persisted on the sidecar, so a re-run would
                # invent tree metrics from the block list and silently
                # demote legitimate `image_enrichment_promoted` PASSes
                # (Finding 5, audit 2026-07-21).  Driving REASON_POLICY /
                # HARD_FAIL_DEFECTS off the typed defect gives the same
                # defect -> verdict consistency guarantee classify_verdict
                # enforces, without fabricating the metrics it cannot
                # reproduce.
                stored_defect = _defect_from_reason_str(verdict_reason)
                gate_result = TreeGateResult(
                    ok=stored_defect == TreeDefect.OK,
                    defect=stored_defect,
                    all_defects=(
                        frozenset()
                        if stored_defect == TreeDefect.OK
                        else frozenset({stored_defect})
                    ),
                )
                if gate_result.defect in HARD_FAIL_DEFECTS:
                    # Hard-fails are terminal in classify_verdict regardless
                    # of the prior verdict — mirror that here.
                    verdict = "FAIL"
                elif (
                    REASON_POLICY.get(gate_result.defect) is _ReasonPolicy.CAP_MARGINAL
                    and verdict == "PASS"
                ):
                    # CAP_MARGINAL defects (bidi_degraded) cap a PASS at
                    # MARGINAL and never upgrade a worse verdict.
                    verdict = "MARGINAL"
                mlr = data.get("max_leaf_ratio", 0.0)
            else:
                structure = data.get("structure") or []
                # Zone-8 Target 8: re-run validate_tree on stored structure
                # and pass its result to classify_verdict instead of None.
                # Prevents silently promoting gate-rejected docs.
                vt_result = validate_tree(structure)
                verdict, verdict_reason = classify_verdict(structure, content_class, vt_result)
                _, _, mlr = _tree_max_leaf_ratio(structure)

            verdict_computed_at = datetime.now(UTC).isoformat()

            # Zone-verdict-persistence: route verdict fields through
            # write_verdict (the sole verdict-mutation entry point) so
            # artifact and sidecar stay in sync.
            write_verdict(
                did,
                verdict,
                verdict_reason,
                CURRENT_PIPELINE_VERSION,
                verdict_computed_at,
                mlr,
                content_class=content_class or None,
            )

            # Non-verdict provenance through save_doc_meta (read-merge-write
            # preserves existing non-verdict fields without overwriting the
            # verdict fields just written by write_verdict).
            provenance_meta = {
                "doc_id": did,
                "doc_name": data.get("doc_name", ""),
                "source_url": data.get("source_url", ""),
                "processed_at": data.get("processed_at", ""),
            }
            if content_class:
                provenance_meta["content_class"] = content_class
            save_doc_meta(did, provenance_meta)
            updated += 1
            print(f"  {did}: {verdict} ({verdict_reason or 'clean'})", flush=True)
        except Exception as e:
            errors += 1
            print(f"  {did}: ERROR — {e}", flush=True)

    print(f"\nDone: {updated} updated, {errors} errors", flush=True)


if __name__ == "__main__":
    # RFC-014 D3: recompute verdicts without re-ingestion
    if "--recompute-verdicts" in sys.argv:
        idx = sys.argv.index("--recompute-verdicts")
        rv_doc_id = (
            sys.argv[idx + 1]
            if idx + 1 < len(sys.argv) and not sys.argv[idx + 1].startswith("--")
            else None
        )
        asyncio.run(recompute_verdicts(rv_doc_id))
        sys.exit(0)

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
                    or (
                        isinstance(exc, (ValueError, RuntimeError))
                        and any(s in str(exc) for s in _NOISE_TRIGGERS)
                    )
                ):
                    return
                _orig(ctx)

            loop.set_exception_handler(_exception_handler)
            runner.run(preprocess(files))
    finally:
        sys.stderr = sys.stderr._wrapped  # type: ignore[union-attr]
