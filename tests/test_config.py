# tests/test_config.py
import os
import importlib
import pytest


def test_settings_has_redis_url(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://myredis:6379/1")
    monkeypatch.setenv("UPLOAD_API_KEY", "secret123")
    import pageindex_mcp.config as cfg
    importlib.reload(cfg)
    assert cfg.settings.redis_url == "redis://myredis:6379/1"
    assert cfg.settings.upload_api_key == "secret123"


def test_settings_redis_defaults(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("UPLOAD_API_KEY", raising=False)
    import pageindex_mcp.config as cfg
    importlib.reload(cfg)
    assert cfg.settings.redis_url == "redis://neonatal-care-redis.neonatal-care:6379/1"
    assert cfg.settings.upload_api_key == ""
