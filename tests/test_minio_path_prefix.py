"""Direct S3 calls must work against a MinIO served under a stripped route prefix.

The minio SDK refuses a path in an endpoint ("path in endpoint is not allowed"),
so the prefix cannot be configured on the client. It is applied one layer lower,
in the HTTP client, after the SigV4 signature is computed — which is exactly
what the reverse proxy undoes before MinIO verifies the request. Same trick as
the presigned-URL splice, applied to every verb instead of just GET.
"""

import importlib
from unittest.mock import patch

import pytest
import urllib3

from pageindex_mcp.minio_client import PrefixedPoolManager, make_minio


class TestPrefixedPoolManager:
    def _capture(self, prefix, url, **kw):
        pm = PrefixedPoolManager(prefix)
        with patch.object(urllib3.PoolManager, "urlopen") as mock:
            pm.urlopen("GET", url, **kw)
        return mock.call_args

    def test_prefix_inserted_before_path(self):
        args = self._capture("/minio", "https://infra.example.com/pageindex/a.pdf")
        assert args.args[1] == "https://infra.example.com/minio/pageindex/a.pdf"

    def test_query_string_preserved_exactly(self):
        """The signature covers the query — rewriting it would invalidate it."""
        url = "https://infra.example.com/pageindex/?list-type=2&prefix=proc%2F"
        args = self._capture("/minio", url)
        assert args.args[1].endswith("?list-type=2&prefix=proc%2F")

    def test_scheme_and_host_untouched(self):
        args = self._capture("/minio", "https://infra.example.com:8443/pageindex/a")
        assert args.args[1].startswith("https://infra.example.com:8443/minio/")

    def test_root_path_handled(self):
        args = self._capture("/minio", "https://infra.example.com/")
        assert args.args[1] == "https://infra.example.com/minio/"

    def test_kwargs_forwarded(self):
        args = self._capture(
            "/minio", "https://infra.example.com/b/k", body=b"x", preload_content=False
        )
        assert args.kwargs["body"] == b"x"
        assert args.kwargs["preload_content"] is False

    def test_already_prefixed_path_not_prefixed_twice(self):
        """urllib3 follows redirects by re-entering urlopen, so a redirect back
        to /minio/... must not become /minio/minio/..."""
        args = self._capture("/minio", "https://infra.example.com/minio/pageindex/a.pdf")
        assert args.args[1] == "https://infra.example.com/minio/pageindex/a.pdf"

    def test_bare_prefix_path_not_prefixed_twice(self):
        args = self._capture("/minio", "https://infra.example.com/minio")
        assert args.args[1] == "https://infra.example.com/minio"

    def test_prefix_lookalike_path_is_still_prefixed(self):
        """/minio-staging is a different path, not an already-prefixed one."""
        args = self._capture("/minio", "https://infra.example.com/minio-staging/a")
        assert args.args[1] == "https://infra.example.com/minio/minio-staging/a"


class TestPrefixedPoolInheritsSdkSettings:
    """Passing http_client= replaces the SDK's own pool, so the prefixed pool
    must carry the same timeout/retry/CA policy or those guarantees silently
    vanish on exactly the deployments that use the public route."""

    def test_timeout_and_retries_match_sdk_defaults(self):
        pm = PrefixedPoolManager("/minio")
        kw = pm.connection_pool_kw

        assert kw["timeout"].connect_timeout == 300
        assert kw["timeout"].read_timeout == 300
        assert kw["maxsize"] == 10
        assert kw["cert_reqs"] == "CERT_REQUIRED"
        assert kw["ca_certs"]
        assert kw["retries"].total == 5
        assert kw["retries"].status_forcelist == [500, 502, 503, 504]

    def test_ssl_cert_file_env_is_honoured(self, monkeypatch, tmp_path):
        ca = tmp_path / "ca.pem"
        ca.write_text("")
        monkeypatch.setenv("SSL_CERT_FILE", str(ca))

        pm = PrefixedPoolManager("/minio")
        assert pm.connection_pool_kw["ca_certs"] == str(ca)

    def test_explicit_kwargs_still_override(self):
        pm = PrefixedPoolManager("/minio", maxsize=3)
        assert pm.connection_pool_kw["maxsize"] == 3


class TestMakeMinio:
    def test_prefix_installs_custom_http_client(self):
        client = make_minio("infra.example.com", "k", "s", secure=True, path_prefix="/minio")
        assert isinstance(client._http, PrefixedPoolManager)

    def test_no_prefix_leaves_default_http_client(self):
        client = make_minio("10.43.0.1:9000", "k", "s", secure=False, path_prefix="")
        assert not isinstance(client._http, PrefixedPoolManager)

    def test_endpoint_with_path_is_still_rejected(self):
        """Guards the reason this module exists — if the SDK ever accepted a
        path, the whole workaround could be dropped."""
        import pytest

        with pytest.raises(ValueError, match="path in endpoint"):
            make_minio("infra.example.com/minio", "k", "s", secure=True, path_prefix="")


@pytest.fixture
def reloadable_config(monkeypatch):
    """Yield pageindex_mcp.config, restoring the module-level singleton after.

    ``importlib.reload`` rebinds ``config.settings``, and monkeypatch only
    rewinds the environment — not the reloaded module. Without this teardown a
    test that reloads under MINIO_PATH_PREFIX=/minio leaves that value visible
    to every later test that reads ``config.settings`` directly.
    """
    import pageindex_mcp.config as cfg

    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None)
    original = cfg.settings
    try:
        yield cfg
    finally:
        cfg.settings = original


class TestConfig:
    def test_minio_path_prefix_defaults_empty(self, monkeypatch, reloadable_config):
        monkeypatch.delenv("MINIO_PATH_PREFIX", raising=False)

        importlib.reload(reloadable_config)
        assert reloadable_config.settings.minio_path_prefix == ""

    def test_minio_path_prefix_normalized(self, monkeypatch, reloadable_config):
        for raw in ("minio", "/minio", "/minio/"):
            monkeypatch.setenv("MINIO_PATH_PREFIX", raw)
            importlib.reload(reloadable_config)
            assert reloadable_config.settings.minio_path_prefix == "/minio", raw

    def test_minio_region_defaults_empty_so_sdk_discovers_it(
        self, monkeypatch, reloadable_config
    ):
        """Pinning a region by default would misSign every request on a
        deployment configured with a different one."""
        monkeypatch.delenv("MINIO_REGION", raising=False)

        importlib.reload(reloadable_config)
        assert reloadable_config.settings.minio_region == ""


class TestPresignFallsBackToMainPrefix:
    """With no separate presign endpoint, presigned URLs are built from the main
    endpoint — so they need the main endpoint's route prefix, or they 404."""

    def test_main_prefix_used_when_no_presign_endpoint(self):
        import pageindex_mcp.storage as storage

        signed = "https://infra.example.com/pageindex/uploads/a.pdf?X-Amz-Signature=abc"
        with patch.object(storage, "settings") as s:
            s.minio_endpoint = "infra.example.com"
            s.minio_path_prefix = "/minio"
            s.minio_presign_endpoint = None
            s.minio_presign_path_prefix = ""
            out = storage._apply_route_prefix(signed)

        assert out == (
            "https://infra.example.com/minio/pageindex/uploads/a.pdf?X-Amz-Signature=abc"
        )

    def test_presign_endpoint_prefix_wins_when_set(self):
        import pageindex_mcp.storage as storage

        signed = "https://public.example.com/pageindex/uploads/a.pdf?X-Amz-Signature=abc"
        with patch.object(storage, "settings") as s:
            s.minio_endpoint = "10.43.0.1:9000"
            s.minio_path_prefix = ""
            s.minio_presign_endpoint = "public.example.com"
            s.minio_presign_path_prefix = "/minio"
            out = storage._apply_route_prefix(signed)

        assert out == (
            "https://public.example.com/minio/pageindex/uploads/a.pdf?X-Amz-Signature=abc"
        )
