"""Direct S3 calls must work against a MinIO served under a stripped route prefix.

The minio SDK refuses a path in an endpoint ("path in endpoint is not allowed"),
so the prefix cannot be configured on the client. It is applied one layer lower,
in the HTTP client, after the SigV4 signature is computed — which is exactly
what the reverse proxy undoes before MinIO verifies the request. Same trick as
the presigned-URL splice, applied to every verb instead of just GET.
"""

import importlib
from unittest.mock import patch

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


class TestConfig:
    def test_minio_path_prefix_defaults_empty(self, monkeypatch):
        monkeypatch.delenv("MINIO_PATH_PREFIX", raising=False)
        monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None)
        import pageindex_mcp.config as cfg

        importlib.reload(cfg)
        assert cfg.settings.minio_path_prefix == ""

    def test_minio_path_prefix_normalized(self, monkeypatch):
        monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None)
        import pageindex_mcp.config as cfg

        for raw in ("minio", "/minio", "/minio/"):
            monkeypatch.setenv("MINIO_PATH_PREFIX", raw)
            importlib.reload(cfg)
            assert cfg.settings.minio_path_prefix == "/minio", raw


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
