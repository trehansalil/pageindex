"""MinIO client construction for endpoints served under a stripped route prefix.

MinIO's public route is ``https://<host>/minio/...`` behind a Traefik
StripPrefix middleware. The prefix cannot go on the client — the SDK rejects it
outright (``ValueError: path in endpoint is not allowed``) — and it must not be
part of the signature either, because the proxy removes it before MinIO
verifies the request.

So it is applied one layer lower, in the HTTP client: SigV4 signs
``/<bucket>/<key>``, this rewrites the wire URL to ``/minio/<bucket>/<key>``,
the proxy strips it back off, and MinIO verifies exactly what was signed. That
makes the public route usable for every S3 verb, not just presigned GETs, with
no port-forward and no second ingress route.
"""

import os
from datetime import timedelta
from urllib.parse import urlsplit, urlunsplit

import certifi
import urllib3
from minio import Minio
from urllib3.util import Retry, Timeout


def _sdk_pool_kwargs(cert_check: bool = True) -> dict:
    """Mirror the PoolManager configuration ``minio.Minio`` builds for itself.

    Passing ``http_client=`` replaces the SDK's pool outright, so a bare
    ``PoolManager()`` would silently drop the five-minute timeout, the
    ``maxsize=10`` pool, the 500/502/503/504 retry policy, and
    ``SSL_CERT_FILE``/certifi CA resolution — but only on the prefixed route,
    which makes the difference hard to spot. Kept in sync with
    ``Minio.__init__``.
    """
    timeout = timedelta(minutes=5).seconds
    return {
        "timeout": Timeout(connect=timeout, read=timeout),
        "maxsize": 10,
        "cert_reqs": "CERT_REQUIRED" if cert_check else "CERT_NONE",
        "ca_certs": os.environ.get("SSL_CERT_FILE") or certifi.where(),
        "retries": Retry(total=5, backoff_factor=0.2, status_forcelist=[500, 502, 503, 504]),
    }


class PrefixedPoolManager(urllib3.PoolManager):
    """PoolManager that prepends a route prefix to every request path.

    Applied after signing, so the signature is unaffected.
    """

    def __init__(self, prefix: str, **kwargs):
        super().__init__(**{**_sdk_pool_kwargs(), **kwargs})
        self._route_prefix = prefix

    def _prefixed_path(self, path: str) -> str:
        """Add the route prefix unless the path already carries it.

        ``urllib3`` follows redirects by re-entering ``self.urlopen``, so
        without this guard a redirect back to ``/minio/<bucket>/<key>`` would
        be rewritten to ``/minio/minio/<bucket>/<key>`` and stop routing.

        Caveat: a bucket literally named after the prefix (``/minio/...`` with
        ``MINIO_PATH_PREFIX=/minio``) is indistinguishable from an
        already-prefixed path here. Name the proxy route something other than a
        real bucket if that ever collides.
        """
        prefix = self._route_prefix
        if path == prefix or path.startswith(prefix + "/"):
            return path
        return prefix + path

    def urlopen(self, method, url, redirect=True, **kw):
        parts = urlsplit(url)
        prefixed = urlunsplit(
            (
                parts.scheme,
                parts.netloc,
                self._prefixed_path(parts.path),
                parts.query,
                parts.fragment,
            )
        )
        return super().urlopen(method, prefixed, redirect=redirect, **kw)


# PLR0913 suppressed: every parameter is a distinct MinIO SDK connection primitive
# passed straight through to ``Minio(...)``; there is no options object at this layer
# to fold ``region`` into without inverting the config dependency and churning callers.
def make_minio(  # noqa: PLR0913
    endpoint: str,
    access_key: str,
    secret_key: str,
    *,
    secure: bool,
    path_prefix: str = "",
    region: str | None = None,
) -> Minio:
    """Build a Minio client, routing through ``path_prefix`` when one is set."""
    http_client = PrefixedPoolManager(path_prefix) if path_prefix else None
    return Minio(
        endpoint,
        access_key=access_key,
        secret_key=secret_key,
        secure=secure,
        region=region,
        # Minio(http_client=None) would replace its own pool manager with None,
        # so only pass the kwarg when there is something to pass.
        **({"http_client": http_client} if http_client else {}),
    )
