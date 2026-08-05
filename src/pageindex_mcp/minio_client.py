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

from urllib.parse import urlsplit, urlunsplit

import urllib3
from minio import Minio


class PrefixedPoolManager(urllib3.PoolManager):
    """PoolManager that prepends a route prefix to every request path.

    Applied after signing, so the signature is unaffected.
    """

    def __init__(self, prefix: str, **kwargs):
        super().__init__(**kwargs)
        self._route_prefix = prefix

    def urlopen(self, method, url, redirect=True, **kw):  # noqa: D102 - urllib3 API
        parts = urlsplit(url)
        prefixed = urlunsplit(
            (parts.scheme, parts.netloc, self._route_prefix + parts.path,
             parts.query, parts.fragment)
        )
        return super().urlopen(method, prefixed, redirect=redirect, **kw)


def make_minio(
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
