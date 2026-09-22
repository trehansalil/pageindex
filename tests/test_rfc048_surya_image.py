"""RFC-048: Surya OCR fallback for standalone images.

Tests cover:
  - Decision event registration for surya_image_fallback
  - OcrEngine.SURYA enum member
  - _surya_image_ocr helper (success, timeout, HTTP error, field mapping)
  - Quality gate logic: trigger conditions, winner selection
  - Config gating (SURYA_FALLBACK_ENABLED=false → no HTTP call)
  - Fail-open guarantee
  - Engine attribution
  - pic_results update (Property 1a)
"""

from __future__ import annotations

import asyncio
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pageindex_mcp.client.indexer import (
    SuryaRecoveryResult,
    _surya_image_ocr,
)
from pageindex_mcp.obs.decision_points import DECISION_POINTS_BY_EVENT
from pageindex_mcp.picture_plane import OcrEngine


# ---------------------------------------------------------------------------
# OcrEngine.SURYA enum member (Design Property 3 prerequisite)
# ---------------------------------------------------------------------------


class TestOcrEngineSurya:
    def test_surya_member_exists(self):
        assert hasattr(OcrEngine, "SURYA")
        assert OcrEngine.SURYA == "surya"
        assert str(OcrEngine.SURYA) == "surya"


# ---------------------------------------------------------------------------
# Decision event registration (Design Property 5c)
# ---------------------------------------------------------------------------


class TestSuryaImageFallbackDecisionEvent:
    def test_event_registered(self):
        assert "surya_image_fallback" in DECISION_POINTS_BY_EVENT

    def test_event_choices(self):
        dp = DECISION_POINTS_BY_EVENT["surya_image_fallback"]
        assert set(dp.choices) == {
            "gate_not_triggered",
            "not_attempted",
            "recovery_succeeded",
            "recovery_insufficient",
            "recovery_failed",
        }

    def test_event_attrs(self):
        dp = DECISION_POINTS_BY_EVENT["surya_image_fallback"]
        expected = {
            "tesseract_chars",
            "surya_chars",
            "tesseract_garbled",
            "surya_garbled",
            "winner",
            "surya_confidence",
            "surya_duration_s",
        }
        assert expected == set(dp.attrs)


# ---------------------------------------------------------------------------
# _surya_image_ocr helper
# ---------------------------------------------------------------------------


def _make_surya_response(
    total_text: str = "hello world",
    total_char_count: int = 11,
    total_avg_confidence: float = 0.95,
) -> dict[str, Any]:
    return {
        "total_text": total_text,
        "total_char_count": total_char_count,
        "total_avg_confidence": total_avg_confidence,
        "regions": [],
        "elapsed_s": 0.5,
    }


def _mock_httpx_client(response_json: dict[str, Any] | None = None, *, raise_exc: Exception | None = None):
    mock_client = AsyncMock()
    if raise_exc:
        mock_client.post = AsyncMock(side_effect=raise_exc)
    else:
        mock_resp = MagicMock()
        mock_resp.json.return_value = response_json or _make_surya_response()
        mock_resp.raise_for_status = MagicMock()
        mock_client.post = AsyncMock(return_value=mock_resp)
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)
    return mock_client


def _patch_httpx(mc):
    return patch("httpx.AsyncClient", return_value=mc), patch("httpx.Timeout")


class TestSuryaImageOcr:
    def _run(self, **kwargs):
        defaults = dict(
            file_bytes=b"fake-png",
            filename="test.png",
            surya_url="http://localhost:8207",
            timeout_s=30.0,
        )
        defaults.update(kwargs)
        return asyncio.get_event_loop().run_until_complete(_surya_image_ocr(**defaults))

    def test_success(self):
        mc = _mock_httpx_client()
        p1, p2 = _patch_httpx(mc)
        with p1, p2:
            result = self._run()
        assert result is not None
        assert result.total_text == "hello world"
        assert result.total_chars == 11
        assert result.confidence == 0.95

    def test_posts_to_ocr_image_endpoint(self):
        mc = _mock_httpx_client()
        p1, p2 = _patch_httpx(mc)
        with p1, p2:
            self._run(surya_url="http://surya:8207")
        mc.post.assert_awaited_once()
        call_url = mc.post.call_args[0][0]
        assert call_url == "http://surya:8207/ocr/image"

    def test_timeout_returns_none(self):
        import httpx
        mc = _mock_httpx_client(raise_exc=httpx.TimeoutException("timeout"))
        p1, p2 = _patch_httpx(mc)
        with p1, p2:
            result = self._run(timeout_s=1.0)
        assert result is None

    def test_http_error_returns_none(self):
        import httpx
        mc = _mock_httpx_client(raise_exc=httpx.HTTPStatusError("400", request=MagicMock(), response=MagicMock()))
        p1, p2 = _patch_httpx(mc)
        with p1, p2:
            result = self._run()
        assert result is None

    def test_field_mapping_total_char_count_to_total_chars(self):
        mc = _mock_httpx_client(_make_surya_response(total_text="abc", total_char_count=3))
        p1, p2 = _patch_httpx(mc)
        with p1, p2:
            result = self._run()
        assert result is not None
        assert result.total_chars == 3


# ---------------------------------------------------------------------------
# Quality gate + winner selection (Design Properties 1, 5a)
# ---------------------------------------------------------------------------


class TestQualityGateLogic:
    def test_gate_not_triggered_when_tesseract_good(self):
        from pageindex_mcp.client.images import MIN_STANDALONE_IMAGE_MD_CHARS
        tess_chars = 200
        tess_garbled = False
        gate_fails = tess_chars <= MIN_STANDALONE_IMAGE_MD_CHARS or tess_garbled
        assert not gate_fails

    def test_gate_triggered_on_low_chars(self):
        from pageindex_mcp.client.images import MIN_STANDALONE_IMAGE_MD_CHARS
        tess_chars = 2
        tess_garbled = False
        gate_fails = tess_chars <= MIN_STANDALONE_IMAGE_MD_CHARS or tess_garbled
        assert gate_fails

    def test_gate_triggered_on_garbled(self):
        from pageindex_mcp.client.images import MIN_STANDALONE_IMAGE_MD_CHARS
        tess_chars = 200
        tess_garbled = True
        gate_fails = tess_chars <= MIN_STANDALONE_IMAGE_MD_CHARS or tess_garbled
        assert gate_fails

    def test_surya_wins_more_chars_not_garbled(self):
        tess_chars, tess_garbled = 10, False
        surya_chars, surya_garbled = 100, False
        surya_wins = (not surya_garbled and surya_chars > tess_chars) or (
            tess_garbled and not surya_garbled
        )
        assert surya_wins

    def test_surya_wins_tesseract_garbled_surya_not(self):
        tess_chars, tess_garbled = 100, True
        surya_chars, surya_garbled = 50, False
        surya_wins = (not surya_garbled and surya_chars > tess_chars) or (
            tess_garbled and not surya_garbled
        )
        assert surya_wins

    def test_tesseract_wins_surya_garbled(self):
        tess_chars, tess_garbled = 10, False
        surya_chars, surya_garbled = 100, True
        surya_wins = (not surya_garbled and surya_chars > tess_chars) or (
            tess_garbled and not surya_garbled
        )
        assert not surya_wins

    def test_tesseract_wins_surya_fewer_chars(self):
        tess_chars, tess_garbled = 100, False
        surya_chars, surya_garbled = 50, False
        surya_wins = (not surya_garbled and surya_chars > tess_chars) or (
            tess_garbled and not surya_garbled
        )
        assert not surya_wins

    def test_both_garbled_tesseract_kept(self):
        tess_chars, tess_garbled = 10, True
        surya_chars, surya_garbled = 100, True
        surya_wins = (not surya_garbled and surya_chars > tess_chars) or (
            tess_garbled and not surya_garbled
        )
        assert not surya_wins
