#!/usr/bin/env python3
"""Comprehensive end-to-end ingestion test driver against REMOTE infrastructure.

This is a *client*: it never converts anything itself, never starts Docker, and
never imports the heavy ingestion stack. It drives the real pipeline over the
network:

    remote_ingest_test.py
        │  POST /upload/files      (X-API-Key)
        ▼
    upload API + arq worker  (host processes, `uv run ...`)
        │  presigned MinIO URL
        ▼
    remote Docling service   (DOCLING_SERVICE_URL, Bearer)
        │
        ▼
    remote MinIO  (processed/<doc_id>.json | .flat.json + .meta.json)

Phases
------
1. ``preflight``  — prove every remote hop is reachable and correctly configured
                    BEFORE burning LLM spend. Includes the presigned-URL
                    round-trip that the remote Docling service depends on.
2. ``discover``   — enumerate source documents (local dir, or a MinIO prefix).
3. ``submit``     — one multipart POST per file so job_id ↔ file stays 1:1.
4. ``poll``       — bounded-concurrency status polling with backoff.
5. ``verify``     — confirm the derived artifacts actually landed in MinIO.
6. ``report``     — console table + JSON + local Markdown/HTML report.

Usage
-----
    # prove the wiring without spending a cent
    uv run python scripts/remote_ingest_test.py --preflight-only

    # ingest everything in doc_store/
    uv run python scripts/remote_ingest_test.py

    # a different local folder, 3 at a time
    uv run python scripts/remote_ingest_test.py --dir ./samples --concurrency 3

    # a folder inside the MinIO pageindex bucket instead
    uv run python scripts/remote_ingest_test.py --source minio --prefix corpus/de/

Every endpoint and credential comes from the environment (``--env-file``,
default ``.env.active``). Nothing is hardcoded; there are no secrets in this file.
"""

from __future__ import annotations

import argparse
import asyncio
import fnmatch
import json
import os
import random
import sys
import time
import uuid
from dataclasses import asdict, dataclass, field, replace
from datetime import UTC, datetime, timedelta
from html import escape
from pathlib import Path
from typing import Any

import httpx

REPO_ROOT = Path(__file__).resolve().parent.parent


def _default_env_file() -> Path:
    """Prefer .env.active — the resolved profile `make env` writes.

    Defaulting to .env would silently read the pre-toggle source, so a run
    invoked as "remote" could quietly target the localhost defaults still
    sitting in .env. Falls back to .env when the profile has not been resolved
    yet, so the script still works in a checkout that never ran `make env`.
    """
    active = REPO_ROOT / ".env.active"
    return active if active.exists() else REPO_ROOT / ".env"


# Mirrors client._SUPPORTED (src/pageindex_mcp/client.py:271-272). Duplicated
# deliberately: this script must stay importable without the ingestion stack.
IMAGE_EXTS = {".png", ".jpg", ".jpeg", ".tiff", ".tif"}
SUPPORTED_EXTS = {
    ".pdf",
    ".md",
    ".markdown",
    ".txt",
    ".docx",
    ".pptx",
    ".html",
    ".xlsx",
} | IMAGE_EXTS

# Extensions that actually exercise the remote Docling service. Anything else
# is converted in-process by the worker and never touches DOCLING_SERVICE_URL.
DOCLING_EXTS = {".pdf"} | IMAGE_EXTS

TERMINAL_STATUSES = {"done", "error"}

DEFAULT_POLL_INTERVAL_S = 5.0
MAX_POLL_INTERVAL_S = 30.0
DEFAULT_JOB_TIMEOUT_S = 1800
DEFAULT_GLOBAL_TIMEOUT_S = 7200
DEFAULT_CONCURRENCY = 2
SUBMIT_RETRIES = 3
POLL_TRANSIENT_TOLERANCE = 10
PRESIGN_PROBE_KEY_PREFIX = "staging/_preflight_probe"

_C = {
    "reset": "\033[0m",
    "dim": "\033[2m",
    "bold": "\033[1m",
    "red": "\033[31m",
    "green": "\033[32m",
    "yellow": "\033[33m",
    "blue": "\033[34m",
    "cyan": "\033[36m",
}
if not sys.stdout.isatty() or os.environ.get("NO_COLOR"):
    _C = dict.fromkeys(_C, "")


def _c(text: str, colour: str) -> str:
    return f"{_C[colour]}{text}{_C['reset']}"


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def log(msg: str, *, level: str = "info") -> None:
    tag = {
        "info": _c("··", "dim"),
        "ok": _c("OK", "green"),
        "warn": _c("!!", "yellow"),
        "fail": _c("XX", "red"),
        "step": _c("▶", "cyan"),
    }[level]
    print(f"{_c(datetime.now().strftime('%H:%M:%S'), 'dim')} {tag} {msg}", flush=True)


# ---------------------------------------------------------------------------
# Configuration
# ---------------------------------------------------------------------------


class ConfigError(RuntimeError):
    """Raised when required configuration is missing or self-contradictory."""


@dataclass(frozen=True)
class RemoteConfig:
    """Every remote hop this script talks to, resolved from the environment."""

    base_url: str
    upload_api_key: str
    minio_endpoint: str
    minio_access_key: str
    minio_secret_key: str
    minio_bucket: str
    minio_secure: bool
    minio_presign_endpoint: str | None
    minio_path_prefix: str
    minio_presign_secure: bool
    minio_presign_path_prefix: str
    docling_url: str | None
    docling_token: str
    redis_url: str
    postgres_dsn: str | None
    # Set from --require-remote. Preflight checks read it to decide whether a
    # missing remote hop is a warning or a hard failure: without it a run that
    # silently converted PDFs in-process still reported green.
    require_remote: bool = False

    @property
    def presign_host(self) -> str:
        return self.minio_presign_endpoint or self.minio_endpoint

    def apply_presign_prefix(self, url: str) -> str:
        """Splice the route prefix into an already-signed URL.

        Mirrors ``pageindex_mcp.storage._apply_route_prefix`` so preflight
        exercises the exact URL shape the worker hands to Docling. The prefix
        is stripped by the reverse proxy before MinIO verifies the signature,
        so adding it after signing is what keeps the signature valid.
        """
        if self.minio_presign_endpoint:
            host, prefix = self.minio_presign_endpoint, self.minio_presign_path_prefix
        else:
            host, prefix = self.minio_endpoint, self.minio_path_prefix
        if not prefix or not host:
            return url
        before, _, after = url.partition(host)
        return f"{before}{host}{prefix}{after}" if after else url

    def redacted(self) -> dict[str, Any]:
        """Config snapshot safe to embed in a report artifact."""
        d = asdict(self)
        for k in (
            "upload_api_key",
            "minio_access_key",
            "minio_secret_key",
            "docling_token",
            "redis_url",
            "postgres_dsn",
        ):
            v = d.get(k)
            d[k] = f"<set:{len(v)} chars>" if v else "<unset>"
        return d


def _env_bool(name: str, default: bool = False) -> bool:
    raw = os.environ.get(name)
    if raw is None or raw == "":
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def load_env_file(path: Path) -> None:
    """Populate os.environ from a dotenv file without clobbering real env vars."""
    if not path.exists():
        return
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        key = key.strip()
        value = value.strip().strip('"').strip("'")
        os.environ.setdefault(key, value)


def build_config(args: argparse.Namespace) -> RemoteConfig:
    base_url = (
        args.base_url
        or os.environ.get("INGEST_BASE_URL")
        or f"http://localhost:{os.environ.get('MCP_PORT', '8201')}"
    )

    cfg = RemoteConfig(
        base_url=base_url.rstrip("/"),
        upload_api_key=os.environ.get("UPLOAD_API_KEY", ""),
        minio_endpoint=os.environ.get("MINIO_ENDPOINT", ""),
        minio_access_key=os.environ.get("MINIO_ACCESS_KEY", ""),
        minio_secret_key=os.environ.get("MINIO_SECRET_KEY", ""),
        minio_bucket=os.environ.get("MINIO_BUCKET", "pageindex"),
        minio_secure=_env_bool("MINIO_SECURE", False),
        minio_path_prefix=_route_prefix("MINIO_PATH_PREFIX"),
        minio_presign_endpoint=os.environ.get("MINIO_PRESIGN_ENDPOINT") or None,
        minio_presign_secure=_env_bool("MINIO_PRESIGN_SECURE", True),
        minio_presign_path_prefix=_route_prefix("MINIO_PRESIGN_PATH_PREFIX"),
        docling_url=(os.environ.get("DOCLING_SERVICE_URL") or "").rstrip("/") or None,
        docling_token=os.environ.get("DOCLING_SERVICE_BEARER_TOKEN", ""),
        redis_url=os.environ.get("REDIS_URL", ""),
        postgres_dsn=os.environ.get("POSTGRES_DSN") or None,
    )

    missing = [
        n
        for n, v in (
            ("UPLOAD_API_KEY", cfg.upload_api_key),
            ("MINIO_ENDPOINT", cfg.minio_endpoint),
            ("MINIO_ACCESS_KEY", cfg.minio_access_key),
            ("MINIO_SECRET_KEY", cfg.minio_secret_key),
            ("REDIS_URL", cfg.redis_url),
        )
        if not v
    ]
    if missing:
        raise ConfigError(
            "Missing required environment variables: "
            + ", ".join(missing)
            + f"\nLoad them with --env-file (currently: {args.env_file})."
        )

    if args.require_remote:
        cfg = replace(cfg, require_remote=True)
        # DOCLING_SERVICE_URL included: a localhost Docling is exactly the
        # silent in-process degradation --require-remote exists to catch.
        local = [
            n
            for n, v in (
                ("MINIO_ENDPOINT", cfg.minio_endpoint),
                ("REDIS_URL", cfg.redis_url),
                ("DOCLING_SERVICE_URL", cfg.docling_url or ""),
            )
            if "localhost" in v or "127.0.0.1" in v
        ]
        if local:
            raise ConfigError(
                "--require-remote is set but these still point at this machine: "
                + ", ".join(local)
                + "\nPoint them at the remote infra (or drop --require-remote)."
            )
    return cfg


# ---------------------------------------------------------------------------
# Result model
# ---------------------------------------------------------------------------


@dataclass
class DocResult:
    """One source document's journey through the pipeline."""

    name: str
    source: str
    size_bytes: int
    uses_docling: bool
    job_id: str | None = None
    status: str = "not_submitted"
    doc_id: str | None = None
    content_class: str | None = None
    reason: str | None = None
    error: str | None = None
    submitted_at: float | None = None
    finished_at: float | None = None
    artifacts: dict[str, Any] = field(default_factory=dict)
    poll_count: int = 0

    @property
    def duration_s(self) -> float | None:
        if self.submitted_at is None or self.finished_at is None:
            return None
        return round(self.finished_at - self.submitted_at, 1)

    @property
    def ok(self) -> bool:
        return self.status == "done"


@dataclass
class PreflightCheck:
    name: str
    passed: bool
    detail: str
    fatal: bool = True

    @property
    def blocking(self) -> bool:
        return self.fatal and not self.passed


# ---------------------------------------------------------------------------
# Preflight
# ---------------------------------------------------------------------------


def _route_prefix(var: str) -> str:
    """Normalize a route prefix env var to '' or '/segment'."""
    raw = os.environ.get(var, "").strip().strip("/")
    return f"/{raw}" if raw else ""


def _minio_client(endpoint: str, cfg: RemoteConfig, *, secure: bool, path_prefix: str = ""):
    from minio import Minio  # imported lazily so --help works without deps

    # A public MinIO route is served under a stripped path prefix. The SDK
    # rejects a path in an endpoint, so it is applied in the HTTP client after
    # signing. This reuses pageindex_mcp.minio_client.PrefixedPoolManager rather
    # than re-deriving it: the local copy had drifted, losing both the
    # redirect re-entry guard (urllib3 re-enters urlopen on a 30x, which turned
    # /minio/<bucket>/<key> into /minio/minio/<bucket>/<key> and failed the
    # probe as "minio.write failed") and the SDK's own pool settings (5-minute
    # timeout, maxsize=10, retry on 5xx, certifi CA) — so a slow MinIO hung the
    # preflight instead of reporting cleanly.
    # region pinned: otherwise the SDK resolves it with a live GetBucketLocation.
    kwargs = {}
    if path_prefix:
        from pageindex_mcp.minio_client import PrefixedPoolManager

        kwargs["http_client"] = PrefixedPoolManager(path_prefix)
    return Minio(
        endpoint,
        access_key=cfg.minio_access_key,
        secret_key=cfg.minio_secret_key,
        secure=secure,
        region=os.environ.get("MINIO_REGION", "us-east-1"),
        **kwargs,
    )


def check_minio(cfg: RemoteConfig) -> list[PreflightCheck]:
    out: list[PreflightCheck] = []
    try:
        mc = _minio_client(
            cfg.minio_endpoint, cfg, secure=cfg.minio_secure, path_prefix=cfg.minio_path_prefix
        )
        exists = mc.bucket_exists(cfg.minio_bucket)
        out.append(
            PreflightCheck(
                "minio.reachable", True, f"{cfg.minio_endpoint} (secure={cfg.minio_secure})"
            )
        )
        out.append(
            PreflightCheck(
                "minio.bucket",
                exists,
                f"bucket '{cfg.minio_bucket}'" + ("" if exists else " does not exist"),
            )
        )
    except Exception as exc:
        out.append(
            PreflightCheck(
                "minio.reachable", False, f"{cfg.minio_endpoint}: {type(exc).__name__}: {exc}"
            )
        )
    return out


def check_presign_roundtrip(cfg: RemoteConfig) -> list[PreflightCheck]:
    """The hop that actually breaks in practice.

    The remote Docling service never receives file bytes — it is handed a
    presigned MinIO URL and fetches the object itself. So the presign host must
    be (a) publicly resolvable and (b) serve the object at the *exact* path the
    SigV4 signature covers. A reverse proxy that strips a path prefix silently
    invalidates every signature. Catch that here, not 20 minutes into a run.
    """
    import io

    checks: list[PreflightCheck] = []
    probe_key = f"{PRESIGN_PROBE_KEY_PREFIX}-{uuid.uuid4().hex[:8]}.txt"
    payload = b"pageindex-preflight"
    internal = None
    try:
        internal = _minio_client(
            cfg.minio_endpoint, cfg, secure=cfg.minio_secure, path_prefix=cfg.minio_path_prefix
        )
        internal.put_object(cfg.minio_bucket, probe_key, io.BytesIO(payload), len(payload))
    except Exception as exc:
        checks.append(
            PreflightCheck(
                "minio.write", False, f"cannot stage probe object: {type(exc).__name__}: {exc}"
            )
        )
        return checks
    checks.append(PreflightCheck("minio.write", True, f"staged {probe_key}"))

    try:
        signer = _minio_client(
            cfg.presign_host,
            cfg,
            secure=cfg.minio_presign_secure if cfg.minio_presign_endpoint else cfg.minio_secure,
        )
        url = cfg.apply_presign_prefix(
            signer.presigned_get_object(cfg.minio_bucket, probe_key, expires=timedelta(minutes=5))
        )
        resp = httpx.get(url, timeout=30.0, follow_redirects=True)
        good = resp.status_code == 200 and resp.content == payload
        checks.append(
            PreflightCheck(
                "minio.presign_fetch",
                good,
                f"{cfg.presign_host}{cfg.minio_presign_path_prefix} -> HTTP {resp.status_code}"
                + ("" if good else f" — body: {resp.text[:160]!r}"),
                fatal=bool(cfg.docling_url),
            )
        )
    except Exception as exc:
        checks.append(
            PreflightCheck(
                "minio.presign_fetch",
                False,
                f"{cfg.presign_host}: {type(exc).__name__}: {exc}",
                fatal=bool(cfg.docling_url),
            )
        )
    finally:
        try:
            if internal is not None:
                internal.remove_object(cfg.minio_bucket, probe_key)
        except Exception:
            pass

    if not cfg.minio_presign_endpoint and cfg.docling_url:
        checks.append(
            PreflightCheck(
                "minio.presign_endpoint_set",
                False,
                "MINIO_PRESIGN_ENDPOINT is unset, so presigned URLs embed "
                f"'{cfg.minio_endpoint}' — the remote Docling service cannot resolve that. "
                "Set it to a publicly reachable MinIO host.",
                fatal=False,
            )
        )
    return checks


def check_docling(cfg: RemoteConfig) -> list[PreflightCheck]:
    if not cfg.docling_url:
        return [
            PreflightCheck(
                "docling.configured",
                False,
                "DOCLING_SERVICE_URL unset — PDFs/images would convert in-process "
                "on this machine instead of on the remote service.",
                # Fatal under --require-remote: the run would still pass, having
                # proved nothing about the remote Docling service it claims to
                # exercise. Advisory otherwise, since non-PDF formats are fine.
                fatal=cfg.require_remote,
            )
        ]
    headers = {"Authorization": f"Bearer {cfg.docling_token}"} if cfg.docling_token else {}
    try:
        resp = httpx.get(f"{cfg.docling_url}/health", headers=headers, timeout=30.0)
        ok = resp.status_code == 200
        return [
            PreflightCheck(
                "docling.health",
                ok,
                f"{cfg.docling_url} -> HTTP {resp.status_code} {resp.text[:80]}",
            )
        ]
    except Exception as exc:
        return [
            PreflightCheck(
                "docling.health", False, f"{cfg.docling_url}: {type(exc).__name__}: {exc}"
            )
        ]


def check_redis(cfg: RemoteConfig) -> list[PreflightCheck]:
    try:
        import redis

        client = redis.Redis.from_url(cfg.redis_url, socket_timeout=10)
        client.ping()
        return [PreflightCheck("redis.ping", True, cfg.redis_url.split("@")[-1])]
    except Exception as exc:
        return [PreflightCheck("redis.ping", False, f"{type(exc).__name__}: {exc}")]


def check_upload_api(cfg: RemoteConfig) -> list[PreflightCheck]:
    """Probe the API surface with a request we expect to be rejected cleanly.

    A GET on an impossible job id proves three things at once: the app is up,
    the /upload router is mounted, and our X-API-Key is accepted (401 vs 404).
    """
    url = f"{cfg.base_url}/upload/status/{uuid.uuid4()}"
    try:
        resp = httpx.get(url, headers={"X-API-Key": cfg.upload_api_key}, timeout=20.0)
    except Exception as exc:
        return [
            PreflightCheck(
                "upload_api.reachable",
                False,
                f"{cfg.base_url}: {type(exc).__name__}: {exc} — is the server running "
                "(`uv run gunicorn -c gunicorn.conf.py pageindex_mcp.server:app`)?",
            )
        ]

    if resp.status_code == 404:
        return [PreflightCheck("upload_api.auth", True, f"{cfg.base_url} — key accepted")]
    if resp.status_code == 401:
        return [
            PreflightCheck(
                "upload_api.auth", False, "401 — UPLOAD_API_KEY does not match the server's"
            )
        ]
    if resp.status_code == 503:
        return [
            PreflightCheck(
                "upload_api.auth", False, "503 — server has no UPLOAD_API_KEY configured"
            )
        ]
    return [
        PreflightCheck(
            "upload_api.auth", False, f"unexpected HTTP {resp.status_code}: {resp.text[:160]}"
        )
    ]


def check_postgres(cfg: RemoteConfig) -> list[PreflightCheck]:
    if not cfg.postgres_dsn:
        return [
            PreflightCheck(
                "postgres.configured",
                False,
                "POSTGRES_DSN unset — document registry dual-write is disabled.",
                fatal=False,
            )
        ]
    try:
        import asyncpg

        async def _probe() -> str:
            conn = await asyncpg.connect(cfg.postgres_dsn, timeout=10)
            try:
                return await conn.fetchval("select version()")
            finally:
                await conn.close()

        version = asyncio.run(_probe())
        return [PreflightCheck("postgres.connect", True, version.split(",")[0], fatal=False)]
    except Exception as exc:
        return [
            PreflightCheck("postgres.connect", False, f"{type(exc).__name__}: {exc}", fatal=False)
        ]


def run_preflight(cfg: RemoteConfig, *, skip: set[str]) -> list[PreflightCheck]:
    log("Preflight — verifying every remote hop", level="step")
    checks: list[PreflightCheck] = []
    probes: list[tuple[str, Any]] = [
        ("minio", lambda: check_minio(cfg)),
        ("presign", lambda: check_presign_roundtrip(cfg)),
        ("docling", lambda: check_docling(cfg)),
        ("redis", lambda: check_redis(cfg)),
        ("upload_api", lambda: check_upload_api(cfg)),
        ("postgres", lambda: check_postgres(cfg)),
    ]
    for name, probe in probes:
        if name in skip:
            log(f"skipped {name} (--skip-check)", level="warn")
            continue
        checks.extend(probe())

    for chk in checks:
        print(
            f"    {_c('PASS', 'green') if chk.passed else (_c('FAIL', 'red') if chk.fatal else _c('WARN', 'yellow'))}"
            f"  {chk.name:<30} {chk.detail}"
        )
    return checks


# ---------------------------------------------------------------------------
# Discovery
# ---------------------------------------------------------------------------


@dataclass
class SourceDoc:
    name: str
    origin: str
    size_bytes: int
    read: Any  # callable () -> bytes


def _matches(name: str, include: list[str], exclude: list[str]) -> bool:
    if include and not any(fnmatch.fnmatch(name, pat) for pat in include):
        return False
    return not any(fnmatch.fnmatch(name, pat) for pat in exclude)


def discover_files(paths: list[Path]) -> list[SourceDoc]:
    docs: list[SourceDoc] = []
    for path in paths:
        if not path.is_file():
            raise ConfigError(f"--files {path} is not a file")
        if path.suffix.lower() not in SUPPORTED_EXTS:
            raise ConfigError(f"--files {path.name}: unsupported extension {path.suffix}")
        docs.append(
            SourceDoc(
                name=path.name,
                origin=str(path),
                size_bytes=path.stat().st_size,
                read=lambda p=path: p.read_bytes(),
            )
        )
    return docs


def discover_local(directory: Path, include: list[str], exclude: list[str]) -> list[SourceDoc]:
    if not directory.is_dir():
        raise ConfigError(f"--dir {directory} is not a directory")
    docs: list[SourceDoc] = []
    for path in sorted(directory.iterdir()):
        if not path.is_file() or path.name.startswith("."):
            continue
        if path.suffix.lower() not in SUPPORTED_EXTS:
            log(f"skip {path.name} — unsupported extension {path.suffix}", level="warn")
            continue
        if not _matches(path.name, include, exclude):
            continue
        docs.append(
            SourceDoc(
                name=path.name,
                origin=str(path),
                size_bytes=path.stat().st_size,
                read=lambda p=path: p.read_bytes(),
            )
        )
    return docs


def discover_minio(
    cfg: RemoteConfig, prefix: str, include: list[str], exclude: list[str]
) -> list[SourceDoc]:
    """Enumerate a folder inside the MinIO bucket as the ingestion source.

    Objects are downloaded and re-submitted through the same upload API as local
    files, so both sources exercise an identical code path server-side.
    """
    mc = _minio_client(
        cfg.minio_endpoint, cfg, secure=cfg.minio_secure, path_prefix=cfg.minio_path_prefix
    )
    docs: list[SourceDoc] = []
    for obj in mc.list_objects(cfg.minio_bucket, prefix=prefix, recursive=True):
        if obj.object_name.endswith("/"):
            continue
        name = obj.object_name.rsplit("/", 1)[-1]
        if Path(name).suffix.lower() not in SUPPORTED_EXTS:
            log(f"skip {obj.object_name} — unsupported extension", level="warn")
            continue
        if not _matches(name, include, exclude):
            continue

        def _read(key: str = obj.object_name) -> bytes:
            resp = mc.get_object(cfg.minio_bucket, key)
            try:
                return resp.read()
            finally:
                resp.close()
                resp.release_conn()

        docs.append(
            SourceDoc(
                name=name,
                origin=f"minio://{cfg.minio_bucket}/{obj.object_name}",
                size_bytes=obj.size or 0,
                read=_read,
            )
        )
    return docs


# ---------------------------------------------------------------------------
# Submit + poll
# ---------------------------------------------------------------------------


async def submit_one(
    client: httpx.AsyncClient, cfg: RemoteConfig, doc: SourceDoc, result: DocResult
) -> None:
    payload = await asyncio.to_thread(doc.read)
    last_error = ""
    for attempt in range(1, SUBMIT_RETRIES + 1):
        try:
            resp = await client.post(
                f"{cfg.base_url}/upload/files",
                headers={"X-API-Key": cfg.upload_api_key},
                files=[("files", (doc.name, payload, "application/octet-stream"))],
            )
        except Exception as exc:
            last_error = f"{type(exc).__name__}: {exc}"
        else:
            if resp.status_code == 202:
                entries = resp.json()
                result.job_id = entries[0]["job_id"]
                result.status = "pending"
                result.submitted_at = time.monotonic()
                log(f"submitted {doc.name} -> job {result.job_id}")
                return
            last_error = f"HTTP {resp.status_code}: {resp.text[:200]}"
            if resp.status_code in (400, 401, 403, 413, 503):
                break  # deterministic rejection — retrying cannot help
        if attempt < SUBMIT_RETRIES:
            await asyncio.sleep(2**attempt + random.random())

    result.status = "error"
    result.reason = "submit_failed"
    result.error = last_error
    result.finished_at = time.monotonic()
    log(f"submit failed {doc.name}: {last_error}", level="fail")


async def poll_one(
    client: httpx.AsyncClient,
    cfg: RemoteConfig,
    result: DocResult,
    *,
    job_timeout_s: float,
    poll_interval_s: float,
    deadline: float,
) -> None:
    if not result.job_id:
        return
    interval = poll_interval_s
    transient = 0
    started = result.submitted_at or time.monotonic()

    while True:
        now = time.monotonic()
        if now - started > job_timeout_s:
            result.status, result.reason = "error", "client_job_timeout"
            result.error = f"no terminal status within {job_timeout_s:.0f}s"
            break
        if now > deadline:
            result.status, result.reason = "error", "client_global_timeout"
            result.error = "global run deadline exceeded"
            break

        await asyncio.sleep(interval)
        result.poll_count += 1
        try:
            resp = await client.get(
                f"{cfg.base_url}/upload/status/{result.job_id}",
                headers={"X-API-Key": cfg.upload_api_key},
            )
        except Exception as exc:
            transient += 1
            if transient > POLL_TRANSIENT_TOLERANCE:
                result.status, result.reason = "error", "status_unreachable"
                result.error = f"{type(exc).__name__}: {exc}"
                break
            interval = min(interval * 1.5, MAX_POLL_INTERVAL_S)
            continue

        if resp.status_code != 200:
            transient += 1
            if transient > POLL_TRANSIENT_TOLERANCE:
                result.status, result.reason = "error", "status_http_error"
                result.error = f"HTTP {resp.status_code}: {resp.text[:200]}"
                break
            interval = min(interval * 1.5, MAX_POLL_INTERVAL_S)
            continue

        transient = 0
        interval = poll_interval_s
        body = resp.json()
        status = body.get("status", "unknown")
        if status != result.status:
            log(f"{result.name}: {result.status} -> {status}")
        result.status = status
        if status in TERMINAL_STATUSES:
            result.doc_id = body.get("doc_id")
            result.content_class = body.get("content_class")
            result.reason = body.get("reason")
            result.error = body.get("error")
            break

    result.finished_at = time.monotonic()
    level = "ok" if result.ok else "fail"
    log(
        f"{result.name}: {result.status}"
        + (f" ({result.reason})" if result.reason else "")
        + (f" in {result.duration_s}s" if result.duration_s else ""),
        level=level,
    )


async def run_pipeline(
    cfg: RemoteConfig,
    docs: list[SourceDoc],
    *,
    concurrency: int,
    job_timeout_s: float,
    poll_interval_s: float,
    global_timeout_s: float,
) -> list[DocResult]:
    results = [
        DocResult(
            name=d.name,
            source=d.origin,
            size_bytes=d.size_bytes,
            uses_docling=Path(d.name).suffix.lower() in DOCLING_EXTS,
        )
        for d in docs
    ]
    sem = asyncio.Semaphore(concurrency)
    deadline = time.monotonic() + global_timeout_s

    async with httpx.AsyncClient(timeout=httpx.Timeout(120.0)) as client:

        async def _one(doc: SourceDoc, res: DocResult) -> None:
            async with sem:
                await submit_one(client, cfg, doc, res)
                if res.job_id:
                    await poll_one(
                        client,
                        cfg,
                        res,
                        job_timeout_s=job_timeout_s,
                        poll_interval_s=poll_interval_s,
                        deadline=deadline,
                    )

        await asyncio.gather(*(_one(d, r) for d, r in zip(docs, results)))
    return results


# ---------------------------------------------------------------------------
# Verification
# ---------------------------------------------------------------------------


def verify_artifacts(cfg: RemoteConfig, results: list[DocResult]) -> None:
    """A 'done' status is a claim; MinIO objects are the evidence."""
    done = [r for r in results if r.ok and r.doc_id]
    if not done:
        return
    log(f"Verifying MinIO artifacts for {len(done)} document(s)", level="step")
    mc = _minio_client(
        cfg.minio_endpoint, cfg, secure=cfg.minio_secure, path_prefix=cfg.minio_path_prefix
    )

    for res in done:
        tree_key = (
            f"processed/{res.doc_id}.flat.json"
            if res.content_class
            else f"processed/{res.doc_id}.json"
        )
        found: dict[str, Any] = {}
        for label, key in (("tree", tree_key), ("meta", f"processed/{res.doc_id}.meta.json")):
            try:
                stat = mc.stat_object(cfg.minio_bucket, key)
                found[label] = {"key": key, "size_bytes": stat.size}
            except Exception as exc:
                found[label] = {"key": key, "error": f"{type(exc).__name__}: {exc}"}
        res.artifacts = found

        missing = [k for k, v in found.items() if "error" in v]
        if missing:
            res.status = "error"
            res.reason = res.reason or "artifacts_missing"
            res.error = f"worker reported done but MinIO is missing: {', '.join(missing)}"
            log(f"{res.name}: {res.error}", level="fail")
        else:
            log(
                f"{res.name}: tree {found['tree']['size_bytes']}B, "
                f"meta {found['meta']['size_bytes']}B",
                level="ok",
            )


# ---------------------------------------------------------------------------
# Reporting
# ---------------------------------------------------------------------------


def _summary(results: list[DocResult]) -> dict[str, Any]:
    ok = [r for r in results if r.ok]
    failed = [r for r in results if not r.ok]
    durations = sorted(r.duration_s for r in ok if r.duration_s is not None)
    reasons: dict[str, int] = {}
    for r in failed:
        reasons[r.reason or "unknown"] = reasons.get(r.reason or "unknown", 0) + 1
    return {
        "total": len(results),
        "succeeded": len(ok),
        "failed": len(failed),
        "docling_routed": sum(1 for r in results if r.uses_docling),
        "duration_s": {
            "min": durations[0] if durations else None,
            "median": durations[len(durations) // 2] if durations else None,
            "max": durations[-1] if durations else None,
            "total": round(sum(durations), 1) if durations else None,
        },
        "failure_reasons": reasons,
    }


def print_report(results: list[DocResult], summary: dict[str, Any]) -> None:
    print()
    print(_c("─" * 96, "dim"))
    print(_c(f"{'DOCUMENT':<40}{'STATUS':<10}{'DOC_ID':<26}{'SECS':>7}  NOTE", "bold"))
    print(_c("─" * 96, "dim"))
    for r in sorted(results, key=lambda x: (x.ok, x.name)):
        status = _c(r.status, "green") if r.ok else _c(r.status, "red")
        note = r.reason or r.content_class or ""
        pad = len(status) - len(r.status)
        print(
            f"{r.name[:39]:<40}{status:<{10 + pad}}{(r.doc_id or '—')[:25]:<26}"
            f"{(r.duration_s or 0):>7.1f}  {note}"
        )
    print(_c("─" * 96, "dim"))

    verdict = (
        _c("ALL PASSED", "green")
        if summary["failed"] == 0
        else _c(f"{summary['failed']} FAILED", "red")
    )
    print(f"{summary['succeeded']}/{summary['total']} succeeded — {verdict}")
    if summary["duration_s"]["median"] is not None:
        d = summary["duration_s"]
        print(f"per-doc seconds: min {d['min']} · median {d['median']} · max {d['max']}")
    if summary["failure_reasons"]:
        print(
            "failure reasons: "
            + ", ".join(f"{k}×{v}" for k, v in sorted(summary["failure_reasons"].items()))
        )
    print()


def write_reports(
    out_dir: Path,
    cfg: RemoteConfig,
    results: list[DocResult],
    checks: list[PreflightCheck],
    summary: dict[str, Any],
    formats: set[str],
) -> list[Path]:
    out_dir.mkdir(parents=True, exist_ok=True)
    stamp = datetime.now(UTC).strftime("%Y%m%dT%H%M%SZ")
    payload = {
        "generated_at": _now(),
        "config": cfg.redacted(),
        "preflight": [asdict(c) for c in checks],
        "summary": summary,
        "documents": [{**asdict(r), "duration_s": r.duration_s} for r in results],
    }
    written: list[Path] = []

    if "json" in formats:
        p = out_dir / f"ingest-run-{stamp}.json"
        p.write_text(json.dumps(payload, indent=2), encoding="utf-8")
        written.append(p)

    if "md" in formats:
        lines = [
            f"# Remote ingestion run — {payload['generated_at']}",
            "",
            f"**{summary['succeeded']}/{summary['total']} succeeded** · "
            f"{summary['docling_routed']} routed to the remote Docling service",
            "",
            "## Preflight",
            "",
            "| Check | Result | Detail |",
            "|---|---|---|",
        ]
        lines += [
            f"| `{c.name}` | {'PASS' if c.passed else ('FAIL' if c.fatal else 'WARN')} "
            f"| {c.detail} |"
            for c in checks
        ]
        lines += [
            "",
            "## Documents",
            "",
            "| Document | Status | doc_id | Seconds | Note |",
            "|---|---|---|---|---|",
        ]
        lines += [
            f"| {r.name} | {r.status} | `{r.doc_id or '—'}` | {r.duration_s or '—'} "
            f"| {r.reason or r.content_class or ''} |"
            for r in sorted(results, key=lambda x: (x.ok, x.name))
        ]
        failures = [r for r in results if not r.ok]
        if failures:
            lines += ["", "## Failures", ""]
            for r in failures:
                lines += [
                    f"### {r.name}",
                    "",
                    f"- reason: `{r.reason or 'unknown'}`",
                    f"- error: `{(r.error or '').strip()[:500]}`",
                    "",
                ]
        p = out_dir / f"ingest-run-{stamp}.md"
        p.write_text("\n".join(lines) + "\n", encoding="utf-8")
        written.append(p)

    if "html" in formats:
        rows = "\n".join(
            f"<tr class='{'ok' if r.ok else 'bad'}'><td>{escape(r.name)}</td>"
            f"<td>{escape(r.status)}</td><td><code>{escape(r.doc_id or '—')}</code></td>"
            f"<td>{r.duration_s or '—'}</td><td>{escape(r.reason or r.content_class or '')}</td></tr>"
            for r in sorted(results, key=lambda x: (x.ok, x.name))
        )
        pre = "\n".join(
            f"<tr class='{'ok' if c.passed else ('bad' if c.fatal else 'warn')}'>"
            f"<td><code>{escape(c.name)}</code></td>"
            f"<td>{'PASS' if c.passed else ('FAIL' if c.fatal else 'WARN')}</td>"
            f"<td>{escape(c.detail)}</td></tr>"
            for c in checks
        )
        p = out_dir / f"ingest-run-{stamp}.html"
        p.write_text(
            "<!doctype html><meta charset='utf-8'>"
            f"<title>Remote ingestion run {stamp}</title>"
            "<style>body{font:15px/1.55 system-ui,sans-serif;margin:2rem auto;max-width:60rem;"
            "padding:0 1rem}table{border-collapse:collapse;width:100%;margin:1rem 0}"
            "th,td{border:1px solid #d0d7de;padding:.45rem .6rem;text-align:left}"
            "th{background:#f6f8fa}tr.ok td:nth-child(2){color:#1a7f37;font-weight:600}"
            "tr.bad td:nth-child(2){color:#cf222e;font-weight:600}"
            "tr.warn td:nth-child(2){color:#9a6700;font-weight:600}"
            "code{font-size:.9em}"
            "@media(prefers-color-scheme:dark){body{background:#0d1117;color:#e6edf3}"
            "th{background:#161b22}th,td{border-color:#30363d}}</style>"
            f"<h1>Remote ingestion run</h1><p>{escape(payload['generated_at'])} — "
            f"<strong>{summary['succeeded']}/{summary['total']} succeeded</strong>, "
            f"{summary['docling_routed']} routed to the remote Docling service.</p>"
            f"<h2>Preflight</h2><table><tr><th>Check</th><th>Result</th><th>Detail</th></tr>{pre}</table>"
            f"<h2>Documents</h2><table><tr><th>Document</th><th>Status</th><th>doc_id</th>"
            f"<th>Seconds</th><th>Note</th></tr>{rows}</table>",
            encoding="utf-8",
        )
        written.append(p)

    return written


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(
        prog="remote_ingest_test.py",
        description="End-to-end ingestion test against remote MinIO + remote Docling.",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__.split("Usage\n-----\n", 1)[-1],
    )

    src = p.add_argument_group("source")
    src.add_argument(
        "--source",
        choices=("local", "minio"),
        default="local",
        help="where documents come from (default: local)",
    )
    src.add_argument(
        "--dir",
        type=Path,
        default=REPO_ROOT / "doc_store",
        help="local source directory (default: ./doc_store)",
    )
    src.add_argument(
        "--files",
        type=Path,
        nargs="+",
        default=None,
        metavar="PATH",
        help="explicit file paths to ingest; bypasses --dir/--include/--exclude "
        "(for --source local)",
    )
    src.add_argument(
        "--prefix", default="", help="object-key prefix inside the MinIO bucket, for --source minio"
    )
    src.add_argument(
        "--include",
        action="append",
        default=[],
        metavar="GLOB",
        help="only files matching this glob (repeatable)",
    )
    src.add_argument(
        "--exclude",
        action="append",
        default=[],
        metavar="GLOB",
        help="skip files matching this glob (repeatable)",
    )
    src.add_argument(
        "--limit", type=int, default=0, help="ingest at most N documents (0 = no limit)"
    )

    tgt = p.add_argument_group("target")
    tgt.add_argument(
        "--env-file",
        type=Path,
        default=_default_env_file(),
        help="dotenv file to load (default: ./.env.active if 'make env' has been run, else ./.env)",
    )
    tgt.add_argument(
        "--base-url",
        default=None,
        help="upload API base URL (default: $INGEST_BASE_URL or localhost:$MCP_PORT)",
    )
    tgt.add_argument(
        "--require-remote",
        action="store_true",
        help="abort if MinIO/Redis/Docling still point at localhost, "
        "and treat an unset or unhealthy Docling service as a "
        "preflight failure rather than a warning",
    )

    run = p.add_argument_group("run")
    run.add_argument(
        "--concurrency",
        type=int,
        default=DEFAULT_CONCURRENCY,
        help=f"documents in flight at once (default: {DEFAULT_CONCURRENCY}); "
        "the worker defaults to max_jobs=1, so raising this only deepens "
        "the queue unless PAGEINDEX_WORKER_MAX_JOBS is also raised",
    )
    run.add_argument(
        "--poll-interval",
        type=float,
        default=DEFAULT_POLL_INTERVAL_S,
        help=f"seconds between status polls (default: {DEFAULT_POLL_INTERVAL_S})",
    )
    run.add_argument(
        "--job-timeout",
        type=float,
        default=DEFAULT_JOB_TIMEOUT_S,
        help=f"per-document timeout (default: {DEFAULT_JOB_TIMEOUT_S}s)",
    )
    run.add_argument(
        "--global-timeout",
        type=float,
        default=DEFAULT_GLOBAL_TIMEOUT_S,
        help=f"whole-run timeout (default: {DEFAULT_GLOBAL_TIMEOUT_S}s)",
    )
    run.add_argument(
        "--preflight-only",
        action="store_true",
        help="run the connectivity checks and stop (no ingestion, no spend)",
    )
    run.add_argument(
        "--dry-run", action="store_true", help="preflight + list what would be ingested, then stop"
    )
    run.add_argument(
        "--skip-check",
        action="append",
        default=[],
        choices=("minio", "presign", "docling", "redis", "upload_api", "postgres"),
        help="skip a preflight probe (repeatable)",
    )
    run.add_argument(
        "--force", action="store_true", help="ingest even if a fatal preflight check failed"
    )
    run.add_argument(
        "--no-verify", action="store_true", help="skip the post-run MinIO artifact verification"
    )

    out = p.add_argument_group("output")
    out.add_argument(
        "--report-dir",
        type=Path,
        default=REPO_ROOT / "audit" / "ingest-runs",
        help="where local report artifacts are written",
    )
    out.add_argument(
        "--format",
        default="json,md",
        help="comma-separated report formats: json,md,html (default: json,md)",
    )
    out.add_argument("--no-report", action="store_true", help="do not write report files")
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    load_env_file(args.env_file)

    try:
        cfg = build_config(args)
    except ConfigError as exc:
        log(str(exc), level="fail")
        return 2

    log(f"upload API  {cfg.base_url}")
    log(f"MinIO       {cfg.minio_endpoint} (bucket {cfg.minio_bucket})")
    log(f"presign as  {cfg.presign_host}")
    log(f"Docling     {cfg.docling_url or '<unset — would convert locally>'}")

    checks = run_preflight(cfg, skip=set(args.skip_check))
    blocking = [c for c in checks if c.blocking]
    if blocking and not args.force:
        log(
            f"{len(blocking)} blocking preflight failure(s): "
            + ", ".join(c.name for c in blocking),
            level="fail",
        )
        log("Fix the above, or re-run with --force to ingest anyway.", level="info")
        if not args.no_report:
            for p in write_reports(args.report_dir, cfg, [], checks, _summary([]), {"json", "md"}):
                log(f"report: {p}")
        return 3
    if args.preflight_only:
        log("Preflight complete (--preflight-only).", level="ok")
        return 0

    try:
        if args.source == "minio":
            docs = discover_minio(cfg, args.prefix, args.include, args.exclude)
            origin = f"minio://{cfg.minio_bucket}/{args.prefix}"
        else:
            if args.files:
                docs = discover_files(args.files)
                origin = f"{len(args.files)} file(s)"
            else:
                docs = discover_local(args.dir, args.include, args.exclude)
                origin = str(args.dir)
    except ConfigError as exc:
        log(str(exc), level="fail")
        return 2

    if args.limit:
        docs = docs[: args.limit]
    if not docs:
        log(f"No ingestible documents found in {origin}", level="fail")
        return 4

    total_mb = sum(d.size_bytes for d in docs) / 1e6
    docling_count = sum(1 for d in docs if Path(d.name).suffix.lower() in DOCLING_EXTS)
    log(f"Discovered {len(docs)} document(s) in {origin} ({total_mb:.1f} MB)", level="step")
    for d in docs:
        route = "docling" if Path(d.name).suffix.lower() in DOCLING_EXTS else "in-process"
        print(f"    {d.name:<48} {d.size_bytes / 1024:>9.1f} KB  {_c(route, 'dim')}")
    if docling_count == 0 and cfg.docling_url:
        log(
            "None of these are PDFs or images, so the remote Docling service "
            "will not be exercised by this run.",
            level="warn",
        )

    if args.dry_run:
        log("Dry run — nothing submitted.", level="ok")
        return 0

    started = time.monotonic()
    results = asyncio.run(
        run_pipeline(
            cfg,
            docs,
            concurrency=max(1, args.concurrency),
            job_timeout_s=args.job_timeout,
            poll_interval_s=args.poll_interval,
            global_timeout_s=args.global_timeout,
        )
    )

    if not args.no_verify:
        try:
            verify_artifacts(cfg, results)
        except Exception as exc:
            log(f"artifact verification failed: {type(exc).__name__}: {exc}", level="warn")

    summary = _summary(results)
    summary["wall_clock_s"] = round(time.monotonic() - started, 1)
    print_report(results, summary)

    if not args.no_report:
        formats = {f.strip() for f in args.format.split(",") if f.strip()}
        for p in write_reports(args.report_dir, cfg, results, checks, summary, formats):
            log(f"report: {p}", level="ok")

    return 0 if summary["failed"] == 0 else 1


if __name__ == "__main__":
    sys.exit(main())
