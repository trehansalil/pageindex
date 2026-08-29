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


# ---------------------------------------------------------------------------
# Zone-5 Config Layering: reset_pipeline_config re-reads all 6 formerly-frozen
# fields from os.environ AND refreshes the backward-compat module-level aliases.
# ---------------------------------------------------------------------------

_FORMERLY_FROZEN_FIELDS = {
    # (env_var, pipeline_config_attr, module_alias_name, non_default_env_value, expected_python_value)
    "PDF_INSPECTOR_PRECLASSIFY": ("pdf_inspector_preclassify", "PDF_INSPECTOR_PRECLASSIFY", "1", True),
    "REMOTE_MD_RENORMALIZE": ("remote_md_renormalize", "REMOTE_MD_RENORMALIZE", "0", False),
    "ALLOW_AGPL_FALLBACK": ("allow_agpl_fallback", "ALLOW_AGPL_FALLBACK", "0", False),
    "OCR_ESCALATION_GARBLE": ("ocr_escalation_garble", "OCR_ESCALATION_GARBLE", "0", False),
    "OCR_ESCALATION_PER_PICTURE": ("ocr_escalation_per_picture", "OCR_ESCALATION_PER_PICTURE", "0", False),
    "IMAGE_DOMINANT_OCR_ESCALATION_ENABLED": (
        "image_dominant_ocr_escalation_enabled",
        "IMAGE_DOMINANT_OCR_ESCALATION_ENABLED",
        "0",
        False,
    ),
}


@pytest.mark.parametrize(
    "env_var",
    list(_FORMERLY_FROZEN_FIELDS.keys()),
    ids=list(_FORMERLY_FROZEN_FIELDS.keys()),
)
def test_reset_pipeline_config_rereads_formerly_frozen_field(monkeypatch, env_var):
    """Contract: reset_pipeline_config() re-reads os.environ for each of the
    6 formerly-frozen fields and also updates the module-level backward-compat
    alias to match."""
    attr, alias_name, env_value, expected = _FORMERLY_FROZEN_FIELDS[env_var]

    monkeypatch.setenv(env_var, env_value)

    import pageindex_mcp.config as cfg_mod
    cfg_mod.reset_pipeline_config()

    # pipeline_config attribute must reflect the env override
    assert getattr(cfg_mod.pipeline_config, attr) is expected, (
        f"pipeline_config.{attr} should be {expected} after setting {env_var}={env_value}"
    )

    # module-level backward-compat alias must also reflect the env override
    assert getattr(cfg_mod, alias_name) is expected, (
        f"config.{alias_name} (backward-compat alias) should be {expected} "
        f"after reset_pipeline_config() with {env_var}={env_value}"
    )


# ---------------------------------------------------------------------------
# Zone-5: PipelineConfig.from_env reads garble_digit_floor from env
# ---------------------------------------------------------------------------


def test_pipeline_config_from_env_reads_garble_digit_floor(monkeypatch):
    """Contract: PipelineConfig.from_env() reads GARBLE_DIGIT_FLOOR from
    os.environ (not hardcoded 500)."""
    monkeypatch.setenv("GARBLE_DIGIT_FLOOR", "1000")

    from pageindex_mcp.config import PipelineConfig

    pc = PipelineConfig.from_env()
    assert pc.garble_digit_floor == 1000


# ---------------------------------------------------------------------------
# Zone-5: GarbleConfig.from_config threads garble_digit_floor from PipelineConfig
# ---------------------------------------------------------------------------


def test_garble_config_from_config_threads_garble_digit_floor(monkeypatch):
    """Regression: GarbleConfig.from_config(pipeline_config) must thread
    garble_digit_floor from PipelineConfig rather than hardcoding the default."""
    monkeypatch.setenv("GARBLE_DIGIT_FLOOR", "1000")

    import pageindex_mcp.config as cfg_mod
    cfg_mod.reset_pipeline_config()

    from pageindex_mcp.helpers.garble import GarbleConfig

    gc = GarbleConfig.from_config(cfg_mod.pipeline_config)
    assert gc.garble_digit_floor == 1000, (
        "GarbleConfig.garble_digit_floor should be sourced from PipelineConfig, not hardcoded"
    )


# ---------------------------------------------------------------------------
# Zone-5: effective_config_snapshot includes garble_digit_floor
# ---------------------------------------------------------------------------


def test_effective_config_snapshot_includes_garble_digit_floor(monkeypatch):
    """Contract: effective_config_snapshot() includes garble_digit_floor in the
    sidecar output and reflects the live pipeline_config value."""
    monkeypatch.setenv("GARBLE_DIGIT_FLOOR", "777")

    import pageindex_mcp.config as cfg_mod
    cfg_mod.reset_pipeline_config()

    snap = cfg_mod.effective_config_snapshot()
    assert "garble_digit_floor" in snap, (
        "garble_digit_floor must appear in effective_config_snapshot output"
    )
    assert snap["garble_digit_floor"] == 777, (
        "garble_digit_floor in snapshot should reflect live pipeline_config value (777), "
        f"got {snap['garble_digit_floor']}"
    )


# ---------------------------------------------------------------------------
# Zone-5: pdf_markdown_converters reads from pipeline_config (integration)
# ---------------------------------------------------------------------------


def test_pdf_markdown_converters_consistent_with_pipeline_config(monkeypatch):
    """Integration: pdf_markdown_converters() reads pdf_converter and
    allow_agpl_fallback from the same source (pipeline_config). When
    PDF_CONVERTER=pymupdf4llm and ALLOW_AGPL_FALLBACK=0, the chain must
    NOT contain a pymupdf4llm entry (AGPL is blocked)."""
    import importlib.util
    from unittest.mock import patch

    monkeypatch.setenv("PDF_CONVERTER", "pymupdf4llm")
    monkeypatch.setenv("ALLOW_AGPL_FALLBACK", "0")

    import pageindex_mcp.config as cfg_mod
    cfg_mod.reset_pipeline_config()

    assert cfg_mod.pipeline_config.pdf_converter == "pymupdf4llm"
    assert cfg_mod.pipeline_config.allow_agpl_fallback is False

    # docling not installed => RuntimeError because AGPL blocked
    with patch.object(importlib.util, "find_spec", return_value=None):
        from pageindex_mcp.converters.pipeline import pdf_markdown_converters

        with pytest.raises(RuntimeError, match="ALLOW_AGPL_FALLBACK=false"):
            pdf_markdown_converters()

    # docling installed => chain should only have docling (no pymupdf4llm since AGPL blocked)
    with patch.object(importlib.util, "find_spec", return_value=True):
        chain = pdf_markdown_converters()
        names = [n for n, _, _ in chain]
        assert "pymupdf4llm" not in names, (
            "pymupdf4llm must not appear in chain when ALLOW_AGPL_FALLBACK=false"
        )
        assert "docling" in names


# ---------------------------------------------------------------------------
# Zone (converter-chain fallback + AGPL gating): new PipelineConfig flags
#   AGPL_STRUCTURAL_FALLBACK_ENABLED  -> agpl_structural_fallback_enabled
#   REMOTE_VERSION_ENFORCE            -> remote_version_enforce
# ---------------------------------------------------------------------------


def test_agpl_structural_fallback_enabled_defaults_true(monkeypatch):
    """Contract: unset AGPL_STRUCTURAL_FALLBACK_ENABLED defaults to True, which
    preserves the historical behavior (structural failures always walked the
    chain, AGPL next entry included)."""
    from pageindex_mcp.config import PipelineConfig

    monkeypatch.delenv("AGPL_STRUCTURAL_FALLBACK_ENABLED", raising=False)
    assert PipelineConfig.from_env().agpl_structural_fallback_enabled is True


def test_remote_version_enforce_defaults_false(monkeypatch):
    """Contract: unset REMOTE_VERSION_ENFORCE defaults to False, keeping the
    remote pipeline_version skew check warn-only."""
    from pageindex_mcp.config import PipelineConfig

    monkeypatch.delenv("REMOTE_VERSION_ENFORCE", raising=False)
    assert PipelineConfig.from_env().remote_version_enforce is False


@pytest.mark.parametrize("raw,expected", [("false", False), ("0", False), ("true", True)])
def test_agpl_structural_fallback_enabled_reads_env(monkeypatch, raw, expected):
    """Contract: the flag is operator-settable from the environment."""
    from pageindex_mcp.config import PipelineConfig

    monkeypatch.setenv("AGPL_STRUCTURAL_FALLBACK_ENABLED", raw)
    assert PipelineConfig.from_env().agpl_structural_fallback_enabled is expected


@pytest.mark.parametrize("raw,expected", [("true", True), ("1", True), ("false", False)])
def test_remote_version_enforce_reads_env(monkeypatch, raw, expected):
    """Contract: the flag is operator-settable from the environment."""
    from pageindex_mcp.config import PipelineConfig

    monkeypatch.setenv("REMOTE_VERSION_ENFORCE", raw)
    assert PipelineConfig.from_env().remote_version_enforce is expected


def test_new_zone_flags_are_declared_fields():
    """Both flags are real declared fields on the frozen PipelineConfig, not
    ad-hoc attributes -- production reads them via dataclasses.replace()."""
    import dataclasses

    from pageindex_mcp.config import PipelineConfig

    names = {f.name for f in dataclasses.fields(PipelineConfig)}
    assert "agpl_structural_fallback_enabled" in names
    assert "remote_version_enforce" in names
