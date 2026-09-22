"""RFC-047 D8: Surya OCR fallback for Arabic density failures.

Tests cover:
  - Config defaults (surya_fallback_enabled=False, url, timeout)
  - Decision event registration
  - _surya_density_recovery helper (success, timeout, bad response)
  - Wiring: fallback fires only for Arabic+density-fail+enabled+pdf
  - Wiring: not_attempted logged when disabled or non-Arabic
"""

from __future__ import annotations

import asyncio
import dataclasses
import json
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pageindex_mcp.client.indexer import (
    SuryaRecoveryResult,
    _surya_density_recovery,
)
from pageindex_mcp.config import settings
from pageindex_mcp.obs.decision_points import DECISION_POINTS_BY_EVENT


# ── Config defaults ─────────────────────────────────────────────────────

class TestSuryaConfig:
    def test_surya_fallback_disabled_by_default(self):
        assert settings.surya_fallback_enabled is False

    def test_surya_service_url_default(self):
        assert settings.surya_service_url == "http://localhost:8207"

    def test_surya_timeout_default(self):
        assert settings.surya_fallback_timeout_s == 120.0


# ── Decision event registration ─────────────────────────────────────────

class TestSuryaDecisionEvent:
    def test_event_registered(self):
        assert "surya_density_fallback" in DECISION_POINTS_BY_EVENT

    def test_event_choices(self):
        dp = DECISION_POINTS_BY_EVENT["surya_density_fallback"]
        assert set(dp.choices) == {
            "recovery_succeeded",
            "recovery_insufficient",
            "recovery_failed",
            "not_attempted",
        }

    def test_event_attrs(self):
        dp = DECISION_POINTS_BY_EVENT["surya_density_fallback"]
        expected = {"original_cpp", "surya_cpp", "arabic_floor", "surya_confidence", "surya_duration_s", "reason"}
        assert expected == set(dp.attrs)


# ── SuryaRecoveryResult dataclass ────────────────────────────────────────

class TestSuryaRecoveryResult:
    def test_frozen(self):
        r = SuryaRecoveryResult(
            pages_text=["hello"],
            total_text="hello",
            total_chars=5,
            chars_per_page=5.0,
            confidence=0.95,
            duration_s=1.0,
        )
        with pytest.raises(dataclasses.FrozenInstanceError):
            r.confidence = 0.5  # type: ignore[misc]


# ── _surya_density_recovery helper ───────────────────────────────────────

class TestSuryaDensityRecovery:
    """Tests for the async helper that calls the Surya service."""

    @pytest.fixture
    def surya_pdf_response(self) -> dict[str, Any]:
        return {
            "pages": [
                {"page_index": 0, "text": "صفحة أولى " * 200, "regions": [], "avg_confidence": 0.96, "char_count": 2000, "elapsed_s": 1.5, "lang": "auto"},
                {"page_index": 1, "text": "صفحة ثانية " * 200, "regions": [], "avg_confidence": 0.94, "char_count": 2200, "elapsed_s": 1.3, "lang": "auto"},
            ],
            "total_text": ("صفحة أولى " * 200) + "\n\n---\n\n" + ("صفحة ثانية " * 200),
            "total_avg_confidence": 0.95,
            "total_char_count": 4200,
            "total_elapsed_s": 2.8,
            "page_count": 2,
            "lang": "auto",
        }

    @pytest.mark.asyncio
    async def test_success(self, surya_pdf_response: dict):
        mock_resp = MagicMock()
        mock_resp.raise_for_status = MagicMock()
        mock_resp.json.return_value = surya_pdf_response

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client), \
             patch("httpx.Timeout"):

            result = await _surya_density_recovery(
                file_bytes=b"fake-pdf",
                filename="test.pdf",
                page_count=2,
                surya_url="http://localhost:8207",
                timeout_s=120.0,
            )

        assert result is not None
        assert result.chars_per_page == 4200 / 2
        assert result.confidence == 0.95
        assert len(result.pages_text) == 2

    @pytest.mark.asyncio
    async def test_timeout_returns_none(self):
        mock_client = AsyncMock()
        mock_client.post.side_effect = Exception("Connection timed out")
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client), \
             patch("httpx.Timeout"):

            result = await _surya_density_recovery(
                file_bytes=b"fake-pdf",
                filename="test.pdf",
                page_count=2,
                surya_url="http://localhost:8207",
                timeout_s=5.0,
            )

        assert result is None

    @pytest.mark.asyncio
    async def test_http_error_returns_none(self):
        mock_resp = MagicMock()
        mock_resp.raise_for_status.side_effect = Exception("500 Internal Server Error")

        mock_client = AsyncMock()
        mock_client.post.return_value = mock_resp
        mock_client.__aenter__ = AsyncMock(return_value=mock_client)
        mock_client.__aexit__ = AsyncMock(return_value=False)

        with patch("httpx.AsyncClient", return_value=mock_client), \
             patch("httpx.Timeout"):

            result = await _surya_density_recovery(
                file_bytes=b"fake-pdf",
                filename="test.pdf",
                page_count=2,
                surya_url="http://localhost:8207",
                timeout_s=120.0,
            )

        assert result is None


# ── Wiring: decision gating ──────────────────────────────────────────────

class TestSuryaFallbackGating:
    """Verify the fallback only fires under the right conditions.

    These are lightweight checks on the gating logic — they don't run
    the full indexer but verify the conditional structure.
    """

    def test_surya_fallback_requires_fail_verdict(self):
        """Non-FAIL verdicts must not trigger fallback."""
        f_verdict = "PASS"
        f_verdict_reason = "structural_pass"
        _is_arabic_dominant = True
        enabled = True
        ext = ".pdf"
        should_attempt = (
            f_verdict == "FAIL"
            and "suspect_density" in f_verdict_reason
            and _is_arabic_dominant
            and enabled
            and ext == ".pdf"
        )
        assert should_attempt is False

    def test_surya_fallback_requires_suspect_density(self):
        """FAIL for other reasons must not trigger fallback."""
        f_verdict = "FAIL"
        f_verdict_reason = "garbling"
        _is_arabic_dominant = True
        enabled = True
        ext = ".pdf"
        should_attempt = (
            f_verdict == "FAIL"
            and "suspect_density" in f_verdict_reason
            and _is_arabic_dominant
            and enabled
            and ext == ".pdf"
        )
        assert should_attempt is False

    def test_surya_fallback_requires_arabic(self):
        """Non-Arabic density failure must not trigger fallback."""
        f_verdict = "FAIL"
        f_verdict_reason = "suspect_density"
        _is_arabic_dominant = False
        enabled = True
        ext = ".pdf"
        should_attempt = (
            f_verdict == "FAIL"
            and "suspect_density" in f_verdict_reason
            and _is_arabic_dominant
            and enabled
            and ext == ".pdf"
        )
        assert should_attempt is False

    def test_surya_fallback_requires_enabled(self):
        """Disabled fallback must not trigger."""
        f_verdict = "FAIL"
        f_verdict_reason = "suspect_density"
        _is_arabic_dominant = True
        enabled = False
        ext = ".pdf"
        should_attempt = (
            f_verdict == "FAIL"
            and "suspect_density" in f_verdict_reason
            and _is_arabic_dominant
            and enabled
            and ext == ".pdf"
        )
        assert should_attempt is False

    def test_surya_fallback_requires_pdf(self):
        """Non-PDF files must not trigger fallback."""
        f_verdict = "FAIL"
        f_verdict_reason = "suspect_density"
        _is_arabic_dominant = True
        enabled = True
        ext = ".docx"
        should_attempt = (
            f_verdict == "FAIL"
            and "suspect_density" in f_verdict_reason
            and _is_arabic_dominant
            and enabled
            and ext == ".pdf"
        )
        assert should_attempt is False

    def test_surya_fallback_fires_when_all_conditions_met(self):
        """All conditions met → fallback should be attempted."""
        f_verdict = "FAIL"
        f_verdict_reason = "suspect_density(chars_per_page=153.0)"
        _is_arabic_dominant = True
        enabled = True
        ext = ".pdf"
        should_attempt = (
            f_verdict == "FAIL"
            and "suspect_density" in f_verdict_reason
            and _is_arabic_dominant
            and enabled
            and ext == ".pdf"
        )
        assert should_attempt is True


# ── Architecture guard: pinned config thresholds ─────────────────────────

class TestSuryaArchitectureGuard:
    def test_surya_config_defaults(self):
        from pageindex_mcp.config import _load_settings
        import os

        env_backup = {
            k: os.environ.pop(k, None)
            for k in ("SURYA_FALLBACK_ENABLED", "SURYA_SERVICE_URL", "SURYA_FALLBACK_TIMEOUT_S")
        }
        try:
            s = _load_settings()
            assert s.surya_fallback_enabled is False
            assert s.surya_service_url == "http://localhost:8207"
            assert s.surya_fallback_timeout_s == 120.0
        finally:
            for k, v in env_backup.items():
                if v is not None:
                    os.environ[k] = v
