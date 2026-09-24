"""MinIO client singleton, presign helpers, and write-barrier."""

from __future__ import annotations

import logging
import time
from datetime import timedelta
from threading import Lock

from minio import Minio  # for type annotations; construction goes through make_minio

from ..config import settings
from ..metrics import (
    WRITE_BARRIER_RETRIES,
)
from ..minio_client import make_minio

logger = logging.getLogger(__name__)

# MinIO's own default region. Only used for the presign client, which cannot
# discover the region live — see _get_presign_minio().
DEFAULT_PRESIGN_REGION = "us-east-1"

_minio_client: Minio | None = None
_minio_lock = Lock()  # guards double-checked locking in get_minio()

# RFC-036 D1: reduced from (0.1, 0.3, 1.0, 3.0) -- 4.4s was over-provisioned for
# MinIO's sub-100ms read-after-write consistency and risked doubling job time
# under arq retry on exhaustion.
_WRITE_BARRIER_DELAYS = (0.05, 0.1, 0.3)


class PersistenceNotVisibleError(RuntimeError):
    """Raised when a MinIO write is still not visible after exhausting retries."""


def _confirm_write_visible(mc: Minio, bucket: str, key: str) -> None:
    """Read-after-write barrier: stat_object with bounded retry + backoff.

    RFC-034 D18: put_object alone races MinIO's read-after-write consistency
    window, causing intermittent persistence-timing ERRORs in the scoring
    pipeline. Follows the confirm-before-destroy pattern already used by
    wipe_processed() (below), but as a positive "confirm the write landed"
    check rather than a pre-delete guard.
    """
    for delay in _WRITE_BARRIER_DELAYS:
        try:
            mc.stat_object(bucket, key)
            return
        except Exception:
            WRITE_BARRIER_RETRIES.inc()
            time.sleep(delay)
    try:
        mc.stat_object(bucket, key)
    except Exception as exc:
        raise PersistenceNotVisibleError(
            f"{key}: not visible in MinIO after {len(_WRITE_BARRIER_DELAYS)} write-barrier retries"
        ) from exc


def get_minio() -> Minio:
    """Lazy singleton: create client and ensure bucket exists on first call."""
    global _minio_client
    if _minio_client is None:
        with _minio_lock:
            if _minio_client is None:
                logger.info(
                    "Initialising MinIO client: endpoint=%s bucket=%s",
                    settings.minio_endpoint,
                    settings.minio_bucket,
                )
                client = make_minio(
                    settings.minio_endpoint,
                    settings.minio_access_key,
                    settings.minio_secret_key,
                    secure=settings.minio_secure,
                    # Set when the endpoint is a reverse-proxied public route
                    # rather than MinIO itself. See minio_client.py.
                    path_prefix=settings.minio_path_prefix,
                    # Deliberately NOT pinned like the presign client below:
                    # this client can reach GetBucketLocation, so leaving the
                    # region unset lets the SDK discover it. Hard-coding
                    # us-east-1 here would sign every request for the wrong
                    # region on a deployment configured with another one.
                    region=settings.minio_region or None,
                )
                if not client.bucket_exists(settings.minio_bucket):
                    logger.info("Creating MinIO bucket: %s", settings.minio_bucket)
                    client.make_bucket(settings.minio_bucket)
                _minio_client = client
    return _minio_client


_presign_client: Minio | None = None
_presign_lock = Lock()


def _get_presign_minio() -> Minio:
    """Return a Minio client for presigned URL generation.

    When ``MINIO_PRESIGN_ENDPOINT`` is set, presigned URLs embed that
    hostname instead of the internal ``MINIO_ENDPOINT``.  This is
    necessary when an external service (outside the cluster) needs to
    download objects via the presigned URL.
    """
    if not settings.minio_presign_endpoint:
        return get_minio()
    global _presign_client
    if _presign_client is None:
        with _presign_lock:
            if _presign_client is None:
                # No path_prefix here: presigned URLs are built from the client's
                # base URL, never sent through its HTTP client, so the prefix is
                # spliced in by _apply_route_prefix instead.
                _presign_client = make_minio(
                    settings.minio_presign_endpoint,
                    settings.minio_access_key,
                    settings.minio_secret_key,
                    # Independent of minio_secure: the internal endpoint is
                    # plaintext in-cluster, the public one is HTTPS.
                    secure=settings.minio_presign_secure,
                    # Pinned: without it the SDK resolves the region with a live
                    # GetBucketLocation against the public host, which raises.
                    # Falls back to us-east-1 (MinIO's own default) when
                    # MINIO_REGION is unset, because "discover it" is not an
                    # option on this route.
                    region=settings.minio_region or DEFAULT_PRESIGN_REGION,
                )
    return _presign_client


def presigned_get_url(object_key: str, expires: timedelta = timedelta(minutes=15)) -> str:
    """Generate a time-limited presigned GET URL for a MinIO object."""
    mc = _get_presign_minio()
    url = mc.presigned_get_object(settings.minio_bucket, object_key, expires=expires)
    return _apply_route_prefix(url)


def _apply_route_prefix(url: str) -> str:
    """Splice ``MINIO_PRESIGN_PATH_PREFIX`` into an already-signed URL.

    MinIO's public route sits behind a Traefik StripPrefix, so MinIO verifies the
    signature against the *stripped* path (``/<bucket>/<key>``) — exactly what the
    SDK signs. Adding the prefix afterwards therefore keeps the signature valid,
    and is the only way to do it: the SDK rejects a path in the endpoint.

    With a dedicated presign endpoint the URL names that host, so its prefix
    applies. Without one the URL is built from the main endpoint, so the main
    endpoint's prefix applies — otherwise a public MINIO_ENDPOINT would presign
    URLs that 404 at the proxy.
    """
    if settings.minio_presign_endpoint:
        host, prefix = settings.minio_presign_endpoint, settings.minio_presign_path_prefix
    else:
        host, prefix = settings.minio_endpoint, settings.minio_path_prefix
    if not prefix or not host:
        return url
    before, _, after = url.partition(host)
    if not after:  # host not found in URL — leave it alone
        return url
    return f"{before}{host}{prefix}{after}"
