"""Presigned URLs must be fetchable by an off-cluster consumer (Scaleway Docling).

The three defects covered here were found by probing the live Traefik route:

  * the presign client took its TLS flag from ``MINIO_SECURE`` (the *internal*
    endpoint), so a public HTTPS presign host produced ``http://`` URLs;
  * it left ``region`` unset, so the SDK issued a live ``GetBucketLocation``
    against the public host on first use and raised before returning a URL;
  * MinIO's public route is served under a StripPrefix'd ``/minio`` prefix. The
    SigV4 signature covers the *stripped* path, so the prefix has to be spliced
    into the URL after signing — and the SDK refuses a path in the endpoint
    ("path in endpoint is not allowed"), so it cannot do this itself.
"""

import importlib
from unittest.mock import MagicMock, patch


class TestPresignSettings:
    def test_presign_secure_defaults_to_true(self, monkeypatch):
        monkeypatch.delenv("MINIO_PRESIGN_SECURE", raising=False)
        monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None)
        import pageindex_mcp.config as cfg

        importlib.reload(cfg)
        assert cfg.settings.minio_presign_secure is True

    def test_presign_secure_read_from_env(self, monkeypatch):
        monkeypatch.setenv("MINIO_PRESIGN_SECURE", "false")
        monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None)
        import pageindex_mcp.config as cfg

        importlib.reload(cfg)
        assert cfg.settings.minio_presign_secure is False

    def test_presign_path_prefix_defaults_to_empty(self, monkeypatch):
        monkeypatch.delenv("MINIO_PRESIGN_PATH_PREFIX", raising=False)
        monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None)
        import pageindex_mcp.config as cfg

        importlib.reload(cfg)
        assert cfg.settings.minio_presign_path_prefix == ""

    def test_presign_path_prefix_normalized(self, monkeypatch):
        """Accept 'minio', '/minio' and '/minio/' — all mean the same route."""
        monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None)
        import pageindex_mcp.config as cfg

        for raw in ("minio", "/minio", "/minio/"):
            monkeypatch.setenv("MINIO_PRESIGN_PATH_PREFIX", raw)
            importlib.reload(cfg)
            assert cfg.settings.minio_presign_path_prefix == "/minio", raw


class TestDoclingUrlNormalization:
    """`{url}/convert/pdf` on a trailing-slash URL yields `//convert/pdf`, which
    the Scaleway function 404s. Observed live against a real conversion call."""

    def test_trailing_slash_stripped(self, monkeypatch):
        monkeypatch.setenv("DOCLING_SERVICE_URL", "https://docling.example.com/")
        monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None)
        import pageindex_mcp.config as cfg

        importlib.reload(cfg)
        assert cfg.settings.docling_service_url == "https://docling.example.com"
        assert f"{cfg.settings.docling_service_url}/convert/pdf" == (
            "https://docling.example.com/convert/pdf"
        )

    def test_unset_stays_none(self, monkeypatch):
        monkeypatch.delenv("DOCLING_SERVICE_URL", raising=False)
        monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None)
        import pageindex_mcp.config as cfg

        importlib.reload(cfg)
        assert cfg.settings.docling_service_url is None


def _presign_settings(mock_settings, **overrides):
    mock_settings.minio_presign_endpoint = "infra.example.com"
    mock_settings.minio_endpoint = "10.43.0.1:9000"
    mock_settings.minio_path_prefix = ""
    mock_settings.minio_bucket = "pageindex"
    mock_settings.minio_access_key = "key"
    mock_settings.minio_secret_key = "secret"
    mock_settings.minio_secure = False  # internal endpoint is plaintext
    mock_settings.minio_presign_secure = True  # public endpoint is HTTPS
    mock_settings.minio_presign_path_prefix = ""
    mock_settings.minio_region = "us-east-1"
    for k, v in overrides.items():
        setattr(mock_settings, k, v)
    return mock_settings


class TestPresignClientConstruction:
    def test_uses_presign_secure_not_minio_secure(self):
        """MINIO_SECURE=false must not downgrade a public HTTPS presign host."""
        import pageindex_mcp.storage as storage

        with (
            patch.object(storage, "_presign_client", None),
            patch.object(storage, "make_minio") as mock_cls,
            patch.object(storage, "settings") as mock_settings,
        ):
            _presign_settings(mock_settings)
            storage._get_presign_minio()

        assert mock_cls.call_args.kwargs["secure"] is True

    def test_pins_region_to_avoid_live_bucket_location_lookup(self):
        """Unset region makes the SDK call GetBucketLocation on the public host,
        which is not routable for that verb — it raised instead of signing."""
        import pageindex_mcp.storage as storage

        with (
            patch.object(storage, "_presign_client", None),
            patch.object(storage, "make_minio") as mock_cls,
            patch.object(storage, "settings") as mock_settings,
        ):
            _presign_settings(mock_settings)
            storage._get_presign_minio()

        assert mock_cls.call_args.kwargs.get("region") == "us-east-1"


class TestPresignPathPrefix:
    def test_prefix_spliced_after_signing(self):
        """Signature covers /pageindex/<key>; the route serves it under /minio."""
        import pageindex_mcp.storage as storage

        mock_client = MagicMock()
        mock_client.presigned_get_object.return_value = (
            "https://infra.example.com/pageindex/uploads/a.pdf?X-Amz-Signature=abc"
        )
        with (
            patch.object(storage, "_get_presign_minio", return_value=mock_client),
            patch.object(storage, "settings") as mock_settings,
        ):
            _presign_settings(mock_settings, minio_presign_path_prefix="/minio")
            url = storage.presigned_get_url("uploads/a.pdf")

        assert url == (
            "https://infra.example.com/minio/pageindex/uploads/a.pdf?X-Amz-Signature=abc"
        )

    def test_query_string_is_untouched(self):
        """Rewriting the query would invalidate the signature."""
        import pageindex_mcp.storage as storage

        signed_query = "X-Amz-Signature=abc&X-Amz-Credential=k%2Fus-east-1&X-Amz-Expires=900"
        mock_client = MagicMock()
        mock_client.presigned_get_object.return_value = (
            f"https://infra.example.com/pageindex/uploads/a.pdf?{signed_query}"
        )
        with (
            patch.object(storage, "_get_presign_minio", return_value=mock_client),
            patch.object(storage, "settings") as mock_settings,
        ):
            _presign_settings(mock_settings, minio_presign_path_prefix="/minio")
            url = storage.presigned_get_url("uploads/a.pdf")

        assert url.split("?", 1)[1] == signed_query

    def test_no_prefix_leaves_url_unchanged(self):
        import pageindex_mcp.storage as storage

        signed = "https://infra.example.com/pageindex/uploads/a.pdf?X-Amz-Signature=abc"
        mock_client = MagicMock()
        mock_client.presigned_get_object.return_value = signed
        with (
            patch.object(storage, "_get_presign_minio", return_value=mock_client),
            patch.object(storage, "settings") as mock_settings,
        ):
            _presign_settings(mock_settings, minio_presign_path_prefix="")
            url = storage.presigned_get_url("uploads/a.pdf")

        assert url == signed

    def test_prefix_ignored_when_endpoint_addresses_minio_directly(self):
        """A ClusterIP endpoint has no route prefix, so nothing is spliced —
        the presign prefix belongs to the presign host, not this one."""
        import pageindex_mcp.storage as storage

        signed = "http://10.43.23.66:9000/pageindex/uploads/a.pdf?X-Amz-Signature=abc"
        mock_client = MagicMock()
        mock_client.presigned_get_object.return_value = signed
        with (
            patch.object(storage, "_get_presign_minio", return_value=mock_client),
            patch.object(storage, "settings") as mock_settings,
        ):
            _presign_settings(
                mock_settings,
                minio_presign_endpoint=None,
                minio_endpoint="10.43.23.66:9000",
                minio_path_prefix="",
                minio_presign_path_prefix="/minio",
            )
            url = storage.presigned_get_url("uploads/a.pdf")

        assert url == signed
