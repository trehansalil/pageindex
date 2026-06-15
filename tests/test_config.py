# tests/test_config.py
import importlib


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
    # Neutralize dotenv at the SOURCE module so importlib.reload(cfg) — which
    # re-executes `from dotenv import load_dotenv` — does not re-read the
    # developer's local .env and re-inject REDIS_URL after delenv cleared it.
    monkeypatch.setattr("dotenv.load_dotenv", lambda *a, **k: None)
    import pageindex_mcp.config as cfg
    importlib.reload(cfg)
    assert cfg.settings.redis_url == "redis://neonatal-care-redis.neonatal-care:6379/1"
    assert cfg.settings.upload_api_key == ""


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
    import pageindex_mcp.config as cfg
    importlib.reload(cfg)
    assert cfg.settings.llm_provider == "compatible"
