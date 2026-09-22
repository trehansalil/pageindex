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
  - The Surya service /ocr/image endpoint (D1)
"""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import pathlib
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pageindex_mcp.client.images import MIN_STANDALONE_IMAGE_MD_CHARS
from pageindex_mcp.client.indexer import (
    SuryaRecoveryResult,
    _image_ocr_quality_gate_fails,
    _surya_beats_tesseract,
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


def _make_surya_result(
    text: str, *, confidence: float = 0.95, duration_s: float = 1.0
) -> SuryaRecoveryResult:
    return SuryaRecoveryResult(
        pages_text=[text],
        total_text=text,
        total_chars=len(text),
        chars_per_page=float(len(text)),
        confidence=confidence,
        duration_s=duration_s,
    )


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
        defaults = {
            "file_bytes": b"fake-png",
            "filename": "test.png",
            "surya_url": "http://localhost:8207",
            "timeout_s": 30.0,
        }
        defaults.update(kwargs)
        # asyncio.run, not get_event_loop: under the full suite another module
        # has already closed the ambient loop, and these tests failed only when
        # run together with it.
        return asyncio.run(_surya_image_ocr(**defaults))

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
#
# These call the production predicates directly -- an inverted condition in
# indexer.py must fail here, which a test that re-derives the expression
# inline cannot do.
# ---------------------------------------------------------------------------


class TestQualityGateLogic:
    def test_gate_not_triggered_when_tesseract_good(self):
        assert not _image_ocr_quality_gate_fails(MIN_STANDALONE_IMAGE_MD_CHARS + 100, False)

    def test_gate_triggered_on_low_chars(self):
        assert _image_ocr_quality_gate_fails(2, False)

    def test_gate_triggered_at_threshold_boundary(self):
        # The gate is `<=`: exactly at the floor still counts as too sparse.
        assert _image_ocr_quality_gate_fails(MIN_STANDALONE_IMAGE_MD_CHARS, False)
        assert not _image_ocr_quality_gate_fails(MIN_STANDALONE_IMAGE_MD_CHARS + 1, False)

    def test_gate_triggered_on_garbled(self):
        # Plentiful characters do not save garbled output.
        assert _image_ocr_quality_gate_fails(MIN_STANDALONE_IMAGE_MD_CHARS + 100, True)


class TestWinnerSelection:
    def test_surya_wins_more_chars_not_garbled(self):
        assert _surya_beats_tesseract(10, False, 100, False)

    def test_surya_wins_tesseract_garbled_surya_not(self):
        # Surya wins on cleanliness even with fewer characters.
        assert _surya_beats_tesseract(100, True, 50, False)

    def test_tesseract_wins_surya_garbled(self):
        assert not _surya_beats_tesseract(10, False, 100, True)

    def test_tesseract_wins_surya_fewer_chars(self):
        assert not _surya_beats_tesseract(100, False, 50, False)

    def test_tesseract_wins_on_tie(self):
        # Equal char counts are not "more" -- the incumbent is kept.
        assert not _surya_beats_tesseract(100, False, 100, False)

    def test_both_garbled_tesseract_kept(self):
        assert not _surya_beats_tesseract(10, True, 100, True)


# ---------------------------------------------------------------------------
# Wiring: the real _convert_to_tree standalone-image branch
# (Design Properties 1, 1a, 2, 3, 4, 5a, 5c)
# ---------------------------------------------------------------------------

_IMAGE_TREE = {
    "structure": [
        {
            "node_id": "0001",
            "title": "Root",
            "text": "content " * 60,
            "nodes": [{"node_id": "0002", "title": "Child", "text": "child " * 60, "nodes": []}],
        }
    ]
}

_SPARSE_MD = "<!-- image -->"
_TESS_POOR = "rn1 ll"
_TESS_GOOD = "Quarterly revenue by region. " * 20
_SURYA_TEXT = "Surya recovered the pie chart labels and their percentages in full."


def _make_image_state():
    from pageindex_mcp.helpers.types import ExtractionState, Route, TreeDefect

    return ExtractionState(
        result={},
        ok=False,
        reason="",
        gate_result=None,
        first_defect=TreeDefect.OK,
        route=Route.TREE,
        md_content=None,
        tmp_md_path=None,
        pic_results=[],
        used_converter=None,
        total_chars=0,
        extraction_stages_captured=[],
    )


def _make_image_client():
    from pageindex_mcp.client import CustomPageIndexClient

    client = CustomPageIndexClient(api_key="test-key")
    client._staging_key = None
    client._run_md_to_tree = AsyncMock(return_value=dict(_IMAGE_TREE))
    client._run_page_index_retrying = AsyncMock(return_value=dict(_IMAGE_TREE))
    return client


@contextlib.contextmanager
def _image_branch(
    *,
    tesseract_text: str,
    surya_result: Any,
    enabled: bool = True,
    garbled_texts: tuple[str, ...] = (),
):
    """Drive the real standalone-image branch with the OCR engines stubbed.

    Only the leaf I/O is replaced: the two Tesseract entry points, the Surya
    HTTP helper, and garble detection (whose own behaviour is pinned in
    tests/test_garble.py). The gate, the comparison and the splice all run
    for real.
    """
    from pageindex_mcp.client import indexer as indexer_mod

    surya_mock = AsyncMock(return_value=surya_result)
    decision_mock = MagicMock()
    splice_inputs: list[str] = []

    def _fake_splice(md, pics):
        splice_inputs.append(md)
        return md.replace("<!-- image -->", pics[0]["ocr_text"])

    def _fake_garble(text, **kwargs):
        return bool(text) and text in garbled_texts

    with (
        patch.object(indexer_mod, "detect_ocr_langs", return_value=["deu", "eng"]),
        patch.object(indexer_mod, "ensure_tessdata", return_value=["deu", "eng"]),
        patch.object(indexer_mod, "image_to_markdown", return_value=_SPARSE_MD),
        patch.object(indexer_mod, "_tesseract_ocr_image", return_value=tesseract_text),
        patch.object(indexer_mod, "_surya_image_ocr", surya_mock),
        patch.object(indexer_mod, "detect_garble", side_effect=_fake_garble),
        patch.object(indexer_mod, "splice_picture_text_for_tree", side_effect=_fake_splice),
        patch.object(indexer_mod, "decision", decision_mock),
        patch.object(
            indexer_mod,
            "settings",
            dataclasses.replace(indexer_mod.settings, surya_fallback_enabled=enabled),
        ),
    ):
        yield SimpleNamespace(
            surya=surya_mock,
            decision=decision_mock,
            splice_inputs=splice_inputs,
        )


def _run_image_branch(client, state, tmp_path):
    img = tmp_path / "pie_chart.png"
    img.write_bytes(b"\x89PNG\r\n\x1a\n-not-a-real-png-but-never-decoded")
    asyncio.run(
        client._convert_to_tree(
            state,
            str(img),
            img.name,
            ".png",
            None,
            None,
        )
    )


def _fallback_events(decision_mock):
    return [
        call.kwargs
        for call in decision_mock.call_args_list
        if call.kwargs.get("event") == "surya_image_fallback"
    ]


class TestSuryaImageFallbackWiring:
    def test_disabled_makes_no_surya_call(self, tmp_path):
        """Property 4: the kill switch must reach the network boundary."""
        state, client = _make_image_state(), _make_image_client()
        with _image_branch(
            tesseract_text=_TESS_POOR, surya_result=None, enabled=False
        ) as h:
            _run_image_branch(client, state, tmp_path)

        h.surya.assert_not_called()
        events = _fallback_events(h.decision)
        assert [e["choice"] for e in events] == ["not_attempted"]
        assert state.pic_results[0]["ocr_text"] == _TESS_POOR
        assert state.ocr_engine == str(OcrEngine.TESSERACT)

    def test_good_tesseract_skips_surya(self, tmp_path):
        """Property 1: healthy Tesseract output must not reach Surya."""
        state, client = _make_image_state(), _make_image_client()
        with _image_branch(tesseract_text=_TESS_GOOD, surya_result=None) as h:
            _run_image_branch(client, state, tmp_path)

        h.surya.assert_not_called()
        assert [e["choice"] for e in _fallback_events(h.decision)] == ["gate_not_triggered"]
        assert state.pic_results[0]["ocr_text"] == _TESS_GOOD
        assert state.ocr_engine == str(OcrEngine.TESSERACT)

    def test_surya_wins_replaces_text_and_engine(self, tmp_path):
        """Properties 3 + 5a: better Surya output is adopted and attributed."""
        state, client = _make_image_state(), _make_image_client()
        with _image_branch(
            tesseract_text=_TESS_POOR,
            surya_result=_make_surya_result(_SURYA_TEXT),
        ) as h:
            _run_image_branch(client, state, tmp_path)

        h.surya.assert_awaited_once()
        assert state.pic_results[0]["ocr_text"] == _SURYA_TEXT
        assert state.ocr_engine == str(OcrEngine.SURYA)

    def test_surya_text_reaches_md_via_splice_not_the_gate(self, tmp_path):
        """Property 1a: the gate rewrites pic_results only; the splice moves text."""
        state, client = _make_image_state(), _make_image_client()
        with _image_branch(
            tesseract_text=_TESS_POOR,
            surya_result=_make_surya_result(_SURYA_TEXT),
        ) as h:
            _run_image_branch(client, state, tmp_path)

        # What the splice was handed still carries the untouched marker: the
        # gate did not edit md_content behind its back.
        assert h.splice_inputs == [_SPARSE_MD]
        # And the Surya text did land in the markdown, via the splice.
        assert _SURYA_TEXT in state.md_content

    def test_surya_worse_keeps_tesseract(self, tmp_path):
        state, client = _make_image_state(), _make_image_client()
        with _image_branch(
            tesseract_text=_TESS_POOR,
            surya_result=_make_surya_result("x"),
        ) as h:
            _run_image_branch(client, state, tmp_path)

        h.surya.assert_awaited_once()
        assert state.pic_results[0]["ocr_text"] == _TESS_POOR
        assert state.ocr_engine == str(OcrEngine.TESSERACT)
        assert [e["choice"] for e in _fallback_events(h.decision)] == ["recovery_insufficient"]

    def test_surya_garbled_keeps_tesseract(self, tmp_path):
        """A longer but garbled Surya result must never displace Tesseract."""
        state, client = _make_image_state(), _make_image_client()
        with _image_branch(
            tesseract_text=_TESS_POOR,
            surya_result=_make_surya_result(_SURYA_TEXT),
            garbled_texts=(_SURYA_TEXT,),
        ) as h:
            _run_image_branch(client, state, tmp_path)

        assert state.pic_results[0]["ocr_text"] == _TESS_POOR
        assert state.ocr_engine == str(OcrEngine.TESSERACT)
        assert [e["choice"] for e in _fallback_events(h.decision)] == ["recovery_insufficient"]

    def test_garbled_tesseract_opens_the_gate(self, tmp_path):
        """Property 1: plentiful-but-garbled Tesseract still calls Surya."""
        state, client = _make_image_state(), _make_image_client()
        with _image_branch(
            tesseract_text=_TESS_GOOD,
            surya_result=_make_surya_result(_SURYA_TEXT),
            garbled_texts=(_TESS_GOOD,),
        ) as h:
            _run_image_branch(client, state, tmp_path)

        h.surya.assert_awaited_once()
        # Surya is shorter than _TESS_GOOD but clean -- it still wins.
        assert state.pic_results[0]["ocr_text"] == _SURYA_TEXT
        assert state.ocr_engine == str(OcrEngine.SURYA)

    def test_surya_failure_is_fail_open(self, tmp_path):
        """Property 2: a dead Surya service degrades, it does not raise."""
        state, client = _make_image_state(), _make_image_client()
        with _image_branch(tesseract_text=_TESS_POOR, surya_result=None) as h:
            _run_image_branch(client, state, tmp_path)

        h.surya.assert_awaited_once()
        assert state.pic_results[0]["ocr_text"] == _TESS_POOR
        assert state.ocr_engine == str(OcrEngine.TESSERACT)
        assert [e["choice"] for e in _fallback_events(h.decision)] == ["recovery_failed"]

    def test_surya_empty_text_is_recovery_failed(self, tmp_path):
        state, client = _make_image_state(), _make_image_client()
        with _image_branch(
            tesseract_text=_TESS_POOR, surya_result=_make_surya_result("   ")
        ) as h:
            _run_image_branch(client, state, tmp_path)

        assert state.pic_results[0]["ocr_text"] == _TESS_POOR
        assert [e["choice"] for e in _fallback_events(h.decision)] == ["recovery_failed"]

    def test_success_event_carries_registered_attrs(self, tmp_path):
        """Property 5c: the emitted event matches its registration."""
        state, client = _make_image_state(), _make_image_client()
        with _image_branch(
            tesseract_text=_TESS_POOR,
            surya_result=_make_surya_result(_SURYA_TEXT, confidence=0.91, duration_s=2.5),
        ) as h:
            _run_image_branch(client, state, tmp_path)

        events = _fallback_events(h.decision)
        assert len(events) == 1
        ev = events[0]
        assert ev["choice"] == "recovery_succeeded"

        point = DECISION_POINTS_BY_EVENT["surya_image_fallback"]
        assert ev["choice"] in point.choices
        assert set(ev["attrs"]) == set(point.attrs)
        assert ev["attrs"]["winner"] == "surya"
        assert ev["attrs"]["tesseract_chars"] == len("".join(_TESS_POOR.split()))
        assert ev["attrs"]["surya_chars"] == len(_SURYA_TEXT)
        assert ev["attrs"]["tesseract_garbled"] is False
        assert ev["attrs"]["surya_garbled"] is False
        assert ev["attrs"]["surya_confidence"] == 0.91
        assert ev["attrs"]["surya_duration_s"] == 2.5


# ---------------------------------------------------------------------------
# D1: the Surya service /ocr/image endpoint (Design Property 6)
#
# Lives in services/surya-ocr-service/app.py, which is a separate package with
# its own lockfile, so it is loaded by path. Surya itself is never imported:
# _ocr_image is stubbed, leaving the HTTP contract -- status codes and response
# shape -- as what these tests pin.
# ---------------------------------------------------------------------------

_SURYA_APP_PATH = (
    pathlib.Path(__file__).resolve().parent.parent
    / "services"
    / "surya-ocr-service"
    / "app.py"
)


@pytest.fixture(scope="module")
def surya_app_module():
    import importlib.util

    spec = importlib.util.spec_from_file_location("surya_ocr_service_app", _SURYA_APP_PATH)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def _png_bytes(size=(40, 20), fmt="PNG") -> bytes:
    import io

    from PIL import Image

    buf = io.BytesIO()
    Image.new("RGB", size, (255, 255, 255)).save(buf, format=fmt)
    return buf.getvalue()


class TestOcrImageEndpoint:
    @contextlib.contextmanager
    def _client(self, module, *, text="chart label", confidences=(0.9, 0.8)):
        from fastapi.testclient import TestClient

        regions = [
            module.RegionResult(text=f"r{i}", confidence=c, bbox=[])
            for i, c in enumerate(confidences)
        ]
        with (
            patch.object(module, "_ocr_image", return_value=(text, regions, 0.25)),
            TestClient(module.app) as client,
        ):
            yield client

    def test_valid_png_returns_text_and_confidence(self, surya_app_module):
        with self._client(surya_app_module) as client:
            resp = client.post(
                "/ocr/image", files={"file": ("chart.png", _png_bytes(), "image/png")}
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_text"] == "chart label"
        assert body["total_char_count"] == len("chart label")
        assert body["total_avg_confidence"] == pytest.approx(0.85)
        assert len(body["regions"]) == 2
        assert body["elapsed_s"] == 0.25

    def test_valid_jpeg_returns_200(self, surya_app_module):
        with self._client(surya_app_module) as client:
            resp = client.post(
                "/ocr/image",
                files={"file": ("chart.jpg", _png_bytes(fmt="JPEG"), "image/jpeg")},
            )
        assert resp.status_code == 200
        assert resp.json()["total_text"] == "chart label"

    def test_no_regions_yields_zero_confidence(self, surya_app_module):
        """An image Surya finds nothing in must not divide by zero."""
        with self._client(surya_app_module, text="", confidences=()) as client:
            resp = client.post(
                "/ocr/image", files={"file": ("blank.png", _png_bytes(), "image/png")}
            )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total_char_count"] == 0
        assert body["total_avg_confidence"] == 0.0

    def test_undecodable_bytes_return_400(self, surya_app_module):
        with self._client(surya_app_module) as client:
            resp = client.post(
                "/ocr/image",
                files={"file": ("junk.png", b"not-an-image-at-all", "image/png")},
            )
        assert resp.status_code == 400
        assert "Invalid image" in resp.json()["detail"]

    def test_empty_file_returns_400(self, surya_app_module):
        with self._client(surya_app_module) as client:
            resp = client.post(
                "/ocr/image", files={"file": ("empty.png", b"", "image/png")}
            )
        assert resp.status_code == 400
        assert resp.json()["detail"] == "Empty file"

    def test_ocr_engine_not_invoked_on_bad_input(self, surya_app_module):
        """Validation must reject before paying for a model pass."""
        from fastapi.testclient import TestClient

        with (
            patch.object(surya_app_module, "_ocr_image") as ocr_mock,
            TestClient(surya_app_module.app) as client,
        ):
            client.post("/ocr/image", files={"file": ("e.png", b"", "image/png")})
            client.post("/ocr/image", files={"file": ("j.png", b"junk", "image/png")})
        ocr_mock.assert_not_called()

    def test_response_shape_matches_ocr_pdf(self, surya_app_module):
        """Property 6: the client reads both responses with one code path."""
        image_fields = set(surya_app_module.ImageOcrResponse.model_fields)
        pdf_fields = set(surya_app_module.PdfOcrResponse.model_fields)
        shared = {"total_text", "total_char_count", "total_avg_confidence"}
        assert shared <= image_fields
        assert shared <= pdf_fields

    def test_response_field_names_match_client_expectations(self, surya_app_module):
        """The names _surya_image_ocr reads must be the names the service emits."""
        emitted = set(surya_app_module.ImageOcrResponse.model_fields)
        assert {"total_text", "total_char_count", "total_avg_confidence"} <= emitted
