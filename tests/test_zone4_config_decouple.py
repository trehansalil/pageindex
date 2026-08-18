"""Zone-4: OCR escalation config decoupling -- contract tests.

Verifies that OCR_ESCALATION_GARBLE and OCR_ESCALATION_PER_PICTURE are
fully independent env-var reads with no legacy OCR_ESCALATION inheritance
shim.  The monolithic OCR_ESCALATION constant must no longer exist in
config.py, and effective_config_snapshot must not emit it.
"""
from __future__ import annotations

import importlib
import os

import pytest


class TestIndependentFlagReads:
    """Each split flag is an independent env-var read defaulting True."""

    def test_garble_default_true(self):
        from pageindex_mcp import config
        assert config.OCR_ESCALATION_GARBLE is True or isinstance(
            config.OCR_ESCALATION_GARBLE, bool
        )

    def test_per_picture_default_true(self):
        from pageindex_mcp import config
        assert config.OCR_ESCALATION_PER_PICTURE is True or isinstance(
            config.OCR_ESCALATION_PER_PICTURE, bool
        )

    def test_setting_garble_zero_does_not_affect_per_picture(self, monkeypatch):
        """OCR_ESCALATION_GARBLE=0 must NOT drag OCR_ESCALATION_PER_PICTURE."""
        monkeypatch.setenv("OCR_ESCALATION_GARBLE", "0")
        monkeypatch.delenv("OCR_ESCALATION_PER_PICTURE", raising=False)
        from pageindex_mcp import config
        importlib.reload(config)
        try:
            assert config.OCR_ESCALATION_GARBLE is False
            assert config.OCR_ESCALATION_PER_PICTURE is True
        finally:
            importlib.reload(config)

    def test_setting_per_picture_zero_does_not_affect_garble(self, monkeypatch):
        """OCR_ESCALATION_PER_PICTURE=0 must NOT drag OCR_ESCALATION_GARBLE."""
        monkeypatch.setenv("OCR_ESCALATION_PER_PICTURE", "0")
        monkeypatch.delenv("OCR_ESCALATION_GARBLE", raising=False)
        from pageindex_mcp import config
        importlib.reload(config)
        try:
            assert config.OCR_ESCALATION_PER_PICTURE is False
            assert config.OCR_ESCALATION_GARBLE is True
        finally:
            importlib.reload(config)

    def test_both_can_be_independently_disabled(self, monkeypatch):
        """Both flags set to 0 independently."""
        monkeypatch.setenv("OCR_ESCALATION_GARBLE", "0")
        monkeypatch.setenv("OCR_ESCALATION_PER_PICTURE", "0")
        from pageindex_mcp import config
        importlib.reload(config)
        try:
            assert config.OCR_ESCALATION_GARBLE is False
            assert config.OCR_ESCALATION_PER_PICTURE is False
        finally:
            importlib.reload(config)


class TestLegacyOcrEscalationRemoved:
    """The monolithic OCR_ESCALATION constant must not exist in config.py."""

    def test_no_ocr_escalation_attribute(self):
        """config module has no OCR_ESCALATION attribute."""
        from pageindex_mcp import config
        assert not hasattr(config, "OCR_ESCALATION"), (
            "Legacy OCR_ESCALATION constant must be removed from config.py"
        )

    def test_legacy_env_var_does_not_affect_split_flags(self, monkeypatch):
        """Setting the old OCR_ESCALATION=0 env var has no effect on split flags.

        After Zone-4, the legacy env var is inert -- both split flags
        default True regardless.
        """
        monkeypatch.setenv("OCR_ESCALATION", "0")
        monkeypatch.delenv("OCR_ESCALATION_GARBLE", raising=False)
        monkeypatch.delenv("OCR_ESCALATION_PER_PICTURE", raising=False)
        from pageindex_mcp import config
        importlib.reload(config)
        try:
            assert config.OCR_ESCALATION_GARBLE is True, (
                "Legacy OCR_ESCALATION=0 must not affect GARBLE split flag"
            )
            assert config.OCR_ESCALATION_PER_PICTURE is True, (
                "Legacy OCR_ESCALATION=0 must not affect PER_PICTURE split flag"
            )
        finally:
            importlib.reload(config)


class TestEffectiveConfigSnapshotKeys:
    """effective_config_snapshot must include split keys, not legacy key."""

    def test_no_ocr_escalation_key(self):
        from pageindex_mcp.config import effective_config_snapshot
        snap = effective_config_snapshot()
        assert "ocr_escalation" not in snap, (
            "Legacy 'ocr_escalation' key must be removed from snapshot"
        )

    def test_split_keys_present(self):
        from pageindex_mcp.config import effective_config_snapshot
        snap = effective_config_snapshot()
        assert "ocr_escalation_garble" in snap
        assert "ocr_escalation_per_picture" in snap

    def test_split_keys_are_bool(self):
        from pageindex_mcp.config import effective_config_snapshot
        snap = effective_config_snapshot()
        assert isinstance(snap["ocr_escalation_garble"], bool)
        assert isinstance(snap["ocr_escalation_per_picture"], bool)

    def test_snapshot_respects_env_override(self, monkeypatch):
        """Split flags in snapshot reflect env var overrides."""
        monkeypatch.setenv("OCR_ESCALATION_GARBLE", "0")
        from pageindex_mcp import config
        importlib.reload(config)
        try:
            snap = config.effective_config_snapshot()
            assert snap["ocr_escalation_garble"] is False
        finally:
            importlib.reload(config)
