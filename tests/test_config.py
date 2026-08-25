# tests/test_config.py
import importlib

import pytest


@pytest.fixture(autouse=True)
def _restore_config_module_identity():
    """Undo the identity damage of importlib.reload(pageindex_mcp.config).

    Reloading a module re-executes its class statements, producing NEW class
    objects (e.g. ``ZDRComplianceError``) distinct from the ones every other
    already-imported module (client/indexer.py, client/remote.py,
    client/llm.py, ...) bound via ``from ..config import ZDRComplianceError``
    at collection time. Left unreverted, this breaks `except
    ZDRComplianceError` / `isinstance` checks in every test that runs after
    this file — the reload is process-global and outlives monkeypatch's env
    rollback. Snapshot the module namespace before each test and restore it
    afterward so downstream tests see the original, collection-time class
    and settings objects again.
    """
    import pageindex_mcp.config as cfg

    snapshot = dict(vars(cfg))
    yield
    cfg.__dict__.clear()
    cfg.__dict__.update(snapshot)


def test_settings_has_redis_url(monkeypatch):
    monkeypatch.setenv("REDIS_URL", "redis://myredis:6379/1")
    monkeypatch.setenv("UPLOAD_API_KEY", "secret123")
    # Neutralize dotenv at the source module so importlib.reload(cfg) does not
    # let a developer's local .env override the env vars set above.
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None)
    import pageindex_mcp.config as cfg

    importlib.reload(cfg)
    assert cfg.settings.redis_url == "redis://myredis:6379/1"
    assert cfg.settings.upload_api_key == "secret123"


def test_settings_redis_defaults(monkeypatch):
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.delenv("UPLOAD_API_KEY", raising=False)
    # Neutralize dotenv at the SOURCE module so importlib.reload(cfg) — which
    # re-executes `from dotenv import load_dotenv` — does not re-read the
    # developer's local .env and re-inject REDIS_URL after delenv cleared it.
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None)
    import pageindex_mcp.config as cfg

    importlib.reload(cfg)
    assert cfg.settings.redis_url == "redis://localhost:6379/0"
    assert cfg.settings.upload_api_key == ""


def test_config_redis_default(monkeypatch):
    """RFC-007 D1 / Property 9: no REDIS_URL env var -> redis://localhost:6379/0."""
    monkeypatch.delenv("REDIS_URL", raising=False)
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None)
    import pageindex_mcp.config as cfg

    importlib.reload(cfg)
    assert cfg.settings.redis_url == "redis://localhost:6379/0"


def test_settings_llm_provider_default_auto(monkeypatch):
    """LLM_PROVIDER defaults to 'auto' when unset."""
    monkeypatch.delenv("LLM_PROVIDER", raising=False)
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None)
    import pageindex_mcp.config as cfg

    importlib.reload(cfg)
    assert cfg.settings.llm_provider == "auto"


def test_settings_llm_provider_normalized(monkeypatch):
    """LLM_PROVIDER is lower-cased and stripped from the environment."""
    monkeypatch.setenv("LLM_PROVIDER", "  Compatible ")
    # Neutralize dotenv so importlib.reload(cfg) cannot let a developer's local
    # .env override LLM_PROVIDER and make this assertion non-deterministic.
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None)
    import pageindex_mcp.config as cfg

    importlib.reload(cfg)
    assert cfg.settings.llm_provider == "compatible"


def test_reset_pipeline_config_refreshes_singleton(monkeypatch):
    """reset_pipeline_config() rebuilds the singleton and propagates to consumer modules."""
    monkeypatch.setenv("ALLOW_AGPL_FALLBACK", "false")
    from pageindex_mcp.config import PipelineConfig, reset_pipeline_config

    reset_pipeline_config()
    from pageindex_mcp.config import pipeline_config

    assert isinstance(pipeline_config, PipelineConfig)
    assert pipeline_config.allow_agpl_fallback is False

    # Verify consumer modules received the refreshed singleton.
    import sys

    for mod_name in (
        "pageindex_mcp.converters.pipeline",
        "pageindex_mcp.converters.pictures",
        "pageindex_mcp.client.recovery",
        "pageindex_mcp.worker.subprocess_mgr",
    ):
        mod = sys.modules.get(mod_name)
        if mod is not None and hasattr(mod, "pipeline_config"):
            assert getattr(mod, "pipeline_config") is pipeline_config, (
                f"{mod_name}.pipeline_config is stale after reset"
            )

    # Flip back and verify refresh
    monkeypatch.setenv("ALLOW_AGPL_FALLBACK", "true")
    reset_pipeline_config()
    from pageindex_mcp.config import pipeline_config as refreshed

    assert refreshed.allow_agpl_fallback is True
