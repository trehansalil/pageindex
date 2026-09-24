# ALLOW-NEW-TEST-FILE: consolidation target for the OCR-fallback cluster
"""Surya OCR fallback — standalone images (RFC-048), Arabic density (RFC-047 D8),
and the OCR spike-evaluation harness (RFC-046 Wave 2 / D1).

Consolidated from test_rfc048_surya_image.py, test_d8_surya_fallback.py and
test_ocr_spike_eval.py.

Covers:
  - Decision-event registration for surya_image_fallback / surya_density_fallback
  - _surya_image_ocr and _surya_density_recovery helpers (success, failure modes)
  - The image quality gate and winner-selection predicates
  - The real _convert_to_tree standalone-image branch (gate, compare, splice)
  - The Surya service /ocr/image endpoint (RFC-048 D1)
  - The Arabic density-fallback gating conjunction (RFC-047 D8)
  - scripts/ocr_spike_eval.py: engine-down hard-fail, production language
    selection, honest comparison labelling, report caveat header
"""

from __future__ import annotations

import ast
import asyncio
import contextlib
import dataclasses
import inspect
import json
import pathlib
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any
from unittest.mock import AsyncMock, MagicMock, patch

import httpx
import pytest
import scripts.ocr_spike_eval as ocr_eval

from pageindex_mcp.client.images import MIN_STANDALONE_IMAGE_MD_CHARS
from pageindex_mcp.client.indexer import (
    SuryaRecoveryResult,
    _image_ocr_quality_gate_fails,
    _surya_beats_tesseract,
    _surya_density_recovery,
    _surya_image_ocr,
)
from pageindex_mcp.config import settings
from pageindex_mcp.converters.ocr_langs import detect_ocr_langs
from pageindex_mcp.obs.decision_points import DECISION_POINTS_BY_EVENT
from pageindex_mcp.picture_plane import OcrEngine

# ===========================================================================
# Decision-event registration (RFC-048 Design Property 5c; RFC-047 D8)
# ===========================================================================


def test_surya_decision_events_are_registered_with_their_choices_and_attrs():
    """Both Surya fallback events must be registered with exactly these shapes.

    Table-driven: every mismatching event is named in one failure so a
    renamed choice or a dropped attr is identifiable without re-running.
    """
    expected = {
        "surya_image_fallback": (
            {
                "gate_not_triggered",
                "not_attempted",
                "recovery_succeeded",
                "recovery_insufficient",
                "recovery_failed",
            },
            {
                "tesseract_chars",
                "surya_chars",
                "tesseract_garbled",
                "surya_garbled",
                "winner",
                "surya_confidence",
                "surya_duration_s",
            },
        ),
        "surya_density_fallback": (
            {
                "recovery_succeeded",
                "recovery_insufficient",
                "recovery_failed",
                "not_attempted",
            },
            {
                "original_cpp",
                "surya_cpp",
                "arabic_floor",
                "surya_confidence",
                "surya_duration_s",
                "reason",
            },
        ),
    }
    failures: list[str] = []
    for event, (choices, attrs) in expected.items():
        dp = DECISION_POINTS_BY_EVENT.get(event)
        if dp is None:
            failures.append(f"{event}: not registered")
            continue
        if set(dp.choices) != choices:
            failures.append(f"{event}: choices {sorted(dp.choices)} != {sorted(choices)}")
        if set(dp.attrs) != attrs:
            failures.append(f"{event}: attrs {sorted(dp.attrs)} != {sorted(attrs)}")
    assert not failures, "; ".join(failures)


# ===========================================================================
# _surya_image_ocr helper (RFC-048)
# ===========================================================================


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


def _mock_httpx_client(
    response_json: dict[str, Any] | None = None, *, raise_exc: Exception | None = None
):
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


def _run_image_ocr(**kwargs):
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


def test_surya_image_ocr_posts_to_ocr_image_and_maps_the_response_fields():
    """Success path: the /ocr/image endpoint, and total_char_count -> total_chars."""
    mc = _mock_httpx_client(_make_surya_response(total_text="abc", total_char_count=3))
    p1, p2 = _patch_httpx(mc)
    with p1, p2:
        result = _run_image_ocr(surya_url="http://surya:8207")

    mc.post.assert_awaited_once()
    assert mc.post.call_args[0][0] == "http://surya:8207/ocr/image"
    assert result is not None
    assert result.total_text == "abc"
    assert result.total_chars == 3
    assert result.confidence == 0.95


def test_surya_image_ocr_returns_none_on_every_transport_failure():
    """Fail-open: neither a timeout nor an HTTP error may propagate."""
    cases = {
        "timeout": httpx.TimeoutException("timeout"),
        "http_status": httpx.HTTPStatusError("400", request=MagicMock(), response=MagicMock()),
    }
    failures = []
    for name, exc in cases.items():
        mc = _mock_httpx_client(raise_exc=exc)
        p1, p2 = _patch_httpx(mc)
        with p1, p2:
            result = _run_image_ocr(timeout_s=1.0)
        if result is not None:
            failures.append(f"{name} -> {result!r}, expected None")
    assert not failures, "; ".join(failures)


# ===========================================================================
# Quality gate + winner selection (RFC-048 Design Properties 1, 5a)
#
# These call the production predicates directly -- an inverted condition in
# indexer.py must fail here, which a test that re-derives the expression
# inline cannot do.
# ===========================================================================


def test_image_ocr_predicates_match_their_decision_tables():
    """_image_ocr_quality_gate_fails and _surya_beats_tesseract, row by row."""
    floor = MIN_STANDALONE_IMAGE_MD_CHARS
    # (tess_chars, tess_garbled) -> gate fails?
    gate_table = [
        ((floor + 100, False), False, "healthy tesseract output"),
        ((2, False), True, "too few characters"),
        ((floor, False), True, "the gate is <=: exactly at the floor is too sparse"),
        ((floor + 1, False), False, "one char above the floor clears it"),
        ((floor + 100, True), True, "plentiful characters do not save garbled output"),
    ]
    # (tess_chars, tess_garbled, surya_chars, surya_garbled) -> surya wins?
    winner_table = [
        ((10, False, 100, False), True, "more chars, clean"),
        ((100, True, 50, False), True, "cleanliness beats length when tesseract is garbled"),
        ((10, False, 100, True), False, "garbled surya never wins"),
        ((100, False, 50, False), False, "fewer chars loses"),
        ((100, False, 100, False), False, "a tie keeps the incumbent"),
        ((10, True, 100, True), False, "both garbled keeps tesseract"),
    ]
    failures = []
    for args, expected, why in gate_table:
        got = _image_ocr_quality_gate_fails(*args)
        if bool(got) is not expected:
            failures.append(
                f"_image_ocr_quality_gate_fails{args} -> {got}, want {expected} ({why})"
            )
    for args, expected, why in winner_table:
        got = _surya_beats_tesseract(*args)
        if bool(got) is not expected:
            failures.append(f"_surya_beats_tesseract{args} -> {got}, want {expected} ({why})")
    assert not failures, "\n".join(failures)


# ===========================================================================
# Wiring: the real _convert_to_tree standalone-image branch
# (RFC-048 Design Properties 1, 1a, 2, 3, 4, 5a, 5c)
# ===========================================================================

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
            dataclasses.replace(
                indexer_mod.settings,
                surya_fallback_enabled=enabled,
                vlm_fallback=False,
            ),
        ),
    ):
        yield SimpleNamespace(
            surya=surya_mock,
            decision=decision_mock,
            splice_inputs=splice_inputs,
        )


def _run_image_branch(client, state, tmp_path, name="pie_chart.png"):
    img = tmp_path / name
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
    def test_surya_is_never_called_when_disabled_or_when_tesseract_is_healthy(self, tmp_path):
        """Properties 1 + 4: the kill switch reaches the network boundary, and
        healthy Tesseract output must not reach Surya at all."""
        failures = []
        cases = [
            (
                "disabled",
                dict(tesseract_text=_TESS_POOR, surya_result=None, enabled=False),
                "not_attempted",
                _TESS_POOR,
            ),
            (
                "good_tesseract",
                dict(tesseract_text=_TESS_GOOD, surya_result=None),
                "gate_not_triggered",
                _TESS_GOOD,
            ),
        ]
        for label, kwargs, expected_choice, expected_text in cases:
            state, client = _make_image_state(), _make_image_client()
            with _image_branch(**kwargs) as h:
                _run_image_branch(client, state, tmp_path, name=f"{label}.png")
            # Pre-VT gate event must match; post-VT may call Surya again
            # when validate_tree condemns the tree (RFC-048 Amendment).
            choices = [e["choice"] for e in _fallback_events(h.decision)]
            if choices != [expected_choice]:
                failures.append(f"{label}: choices {choices} != [{expected_choice!r}]")
            if state.pic_results[0]["ocr_text"] != expected_text:
                failures.append(f"{label}: ocr_text was replaced")
            if state.ocr_engine != str(OcrEngine.TESSERACT):
                failures.append(f"{label}: engine {state.ocr_engine!r} != tesseract")
        assert not failures, "; ".join(failures)

    def test_surya_wins_replaces_text_engine_and_reaches_md_via_the_splice(self, tmp_path):
        """Properties 1a + 3 + 5a: better Surya output is adopted, attributed,
        and moved into the markdown by the splice (not by the gate), with an
        event whose attrs match its registration."""
        state, client = _make_image_state(), _make_image_client()
        with _image_branch(
            tesseract_text=_TESS_POOR,
            surya_result=_make_surya_result(_SURYA_TEXT, confidence=0.91, duration_s=2.5),
        ) as h:
            _run_image_branch(client, state, tmp_path)

        h.surya.assert_awaited_once()
        assert state.pic_results[0]["ocr_text"] == _SURYA_TEXT
        assert state.ocr_engine == str(OcrEngine.SURYA)
        # What the splice was handed still carries the untouched marker: the
        # gate did not edit md_content behind its back.
        assert h.splice_inputs == [_SPARSE_MD]
        # And the Surya text did land in the markdown, via the splice.
        assert _SURYA_TEXT in state.md_content

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

    def test_worse_or_garbled_surya_never_displaces_tesseract(self, tmp_path):
        """recovery_insufficient via both of its routes: fewer characters, and
        a longer-but-garbled result."""
        failures = []
        cases = [
            ("fewer_chars", dict(tesseract_text=_TESS_POOR, surya_result=_make_surya_result("x"))),
            (
                "garbled",
                dict(
                    tesseract_text=_TESS_POOR,
                    surya_result=_make_surya_result(_SURYA_TEXT),
                    garbled_texts=(_SURYA_TEXT,),
                ),
            ),
        ]
        for label, kwargs in cases:
            state, client = _make_image_state(), _make_image_client()
            with _image_branch(**kwargs) as h:
                _run_image_branch(client, state, tmp_path, name=f"{label}.png")
            if not h.surya.await_count:
                failures.append(f"{label}: Surya was not awaited")
            if state.pic_results[0]["ocr_text"] != _TESS_POOR:
                failures.append(f"{label}: tesseract text was displaced")
            if state.ocr_engine != str(OcrEngine.TESSERACT):
                failures.append(f"{label}: engine {state.ocr_engine!r} != tesseract")
            choices = [e["choice"] for e in _fallback_events(h.decision)]
            if choices != ["recovery_insufficient"]:
                failures.append(f"{label}: choices {choices} != ['recovery_insufficient']")
        assert not failures, "; ".join(failures)

    def test_dead_or_empty_surya_is_fail_open(self, tmp_path):
        """Property 2: a dead Surya service, or one returning only whitespace,
        degrades to Tesseract — it does not raise."""
        failures = []
        cases = [
            ("service_error", None),
            ("empty_text", _make_surya_result("   ")),
        ]
        for label, surya_result in cases:
            state, client = _make_image_state(), _make_image_client()
            with _image_branch(tesseract_text=_TESS_POOR, surya_result=surya_result) as h:
                _run_image_branch(client, state, tmp_path, name=f"{label}.png")
            if not h.surya.await_count:
                failures.append(f"{label}: Surya was not awaited")
            if state.pic_results[0]["ocr_text"] != _TESS_POOR:
                failures.append(f"{label}: tesseract text was displaced")
            if state.ocr_engine != str(OcrEngine.TESSERACT):
                failures.append(f"{label}: engine {state.ocr_engine!r} != tesseract")
            choices = [e["choice"] for e in _fallback_events(h.decision)]
            if choices != ["recovery_failed"]:
                failures.append(f"{label}: choices {choices} != ['recovery_failed']")
        assert not failures, "; ".join(failures)

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


# ===========================================================================
# RFC-048 D1: the Surya service /ocr/image endpoint (Design Property 6)
#
# Lives in services/surya-ocr-service/app.py, which is a separate package with
# its own lockfile, so it is loaded by path. Surya itself is never imported:
# _ocr_image is stubbed, leaving the HTTP contract -- status codes and response
# shape -- as what these tests pin.
# ===========================================================================

_SURYA_APP_PATH = (
    pathlib.Path(__file__).resolve().parent.parent / "services" / "surya-ocr-service" / "app.py"
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

    def test_decodable_images_return_text_confidence_and_the_shared_response_shape(
        self, surya_app_module
    ):
        """Property 6: PNG and JPEG both succeed, and the response carries the
        three fields the client reads from /ocr/pdf too — one code path."""
        uploads = {
            "png": ("chart.png", _png_bytes(), "image/png"),
            "jpeg": ("chart.jpg", _png_bytes(fmt="JPEG"), "image/jpeg"),
        }
        failures = []
        with self._client(surya_app_module) as client:
            for label, upload in uploads.items():
                resp = client.post("/ocr/image", files={"file": upload})
                if resp.status_code != 200:
                    failures.append(f"{label}: status {resp.status_code}")
                    continue
                body = resp.json()
                if body["total_text"] != "chart label":
                    failures.append(f"{label}: total_text {body['total_text']!r}")
                if body["total_char_count"] != len("chart label"):
                    failures.append(f"{label}: total_char_count {body['total_char_count']}")
                if body["total_avg_confidence"] != pytest.approx(0.85):
                    failures.append(f"{label}: confidence {body['total_avg_confidence']}")
                if len(body["regions"]) != 2 or body["elapsed_s"] != 0.25:
                    failures.append(
                        f"{label}: regions/elapsed {body['regions']} {body['elapsed_s']}"
                    )
        assert not failures, "; ".join(failures)

        shared = {"total_text", "total_char_count", "total_avg_confidence"}
        image_fields = set(surya_app_module.ImageOcrResponse.model_fields)
        pdf_fields = set(surya_app_module.PdfOcrResponse.model_fields)
        assert shared <= image_fields
        assert shared <= pdf_fields

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

    def test_bad_input_returns_400_before_paying_for_a_model_pass(self, surya_app_module):
        """Empty and undecodable uploads are rejected, and _ocr_image is never
        invoked for either."""
        from fastapi.testclient import TestClient

        cases = {
            "empty": (("empty.png", b"", "image/png"), "Empty file"),
            "undecodable": (("junk.png", b"not-an-image-at-all", "image/png"), "Invalid image"),
        }
        failures = []
        with (
            patch.object(surya_app_module, "_ocr_image") as ocr_mock,
            TestClient(surya_app_module.app) as client,
        ):
            for label, (upload, expected_detail) in cases.items():
                resp = client.post("/ocr/image", files={"file": upload})
                if resp.status_code != 400:
                    failures.append(f"{label}: status {resp.status_code}, want 400")
                elif expected_detail not in resp.json()["detail"]:
                    failures.append(f"{label}: detail {resp.json()['detail']!r}")
        ocr_mock.assert_not_called()
        assert not failures, "; ".join(failures)


# ===========================================================================
# RFC-047 D8: Arabic density fallback
# ===========================================================================


@pytest.fixture
def surya_pdf_response() -> dict[str, Any]:
    return {
        "pages": [
            {
                "page_index": 0,
                "text": "صفحة أولى " * 200,
                "regions": [],
                "avg_confidence": 0.96,
                "char_count": 2000,
                "elapsed_s": 1.5,
                "lang": "auto",
            },
            {
                "page_index": 1,
                "text": "صفحة ثانية " * 200,
                "regions": [],
                "avg_confidence": 0.94,
                "char_count": 2200,
                "elapsed_s": 1.3,
                "lang": "auto",
            },
        ],
        "total_text": ("صفحة أولى " * 200) + "\n\n---\n\n" + ("صفحة ثانية " * 200),
        "total_avg_confidence": 0.95,
        "total_char_count": 4200,
        "total_elapsed_s": 2.8,
        "page_count": 2,
        "lang": "auto",
    }


async def test_surya_density_recovery_maps_the_response_into_a_frozen_result(
    surya_pdf_response: dict,
):
    mock_resp = MagicMock()
    mock_resp.raise_for_status = MagicMock()
    mock_resp.json.return_value = surya_pdf_response

    mock_client = AsyncMock()
    mock_client.post.return_value = mock_resp
    mock_client.__aenter__ = AsyncMock(return_value=mock_client)
    mock_client.__aexit__ = AsyncMock(return_value=False)

    with patch("httpx.AsyncClient", return_value=mock_client), patch("httpx.Timeout"):
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
    # The result the routing decision is taken from must not be mutable
    # downstream of that decision.
    with pytest.raises(dataclasses.FrozenInstanceError):
        result.confidence = 0.5  # type: ignore[misc]


async def test_surya_density_recovery_returns_none_on_transport_and_status_failures():
    """Fail-open: a connection failure and a non-2xx both degrade to None."""

    def _client_raising_on_post(exc):
        mc = AsyncMock()
        mc.post.side_effect = exc
        mc.__aenter__ = AsyncMock(return_value=mc)
        mc.__aexit__ = AsyncMock(return_value=False)
        return mc

    def _client_raising_on_status(exc):
        resp = MagicMock()
        resp.raise_for_status.side_effect = exc
        mc = AsyncMock()
        mc.post.return_value = resp
        mc.__aenter__ = AsyncMock(return_value=mc)
        mc.__aexit__ = AsyncMock(return_value=False)
        return mc

    cases = {
        "connection": _client_raising_on_post(Exception("Connection timed out")),
        "http_status": _client_raising_on_status(Exception("500 Internal Server Error")),
    }
    failures = []
    for label, mock_client in cases.items():
        with patch("httpx.AsyncClient", return_value=mock_client), patch("httpx.Timeout"):
            result = await _surya_density_recovery(
                file_bytes=b"fake-pdf",
                filename="test.pdf",
                page_count=2,
                surya_url="http://localhost:8207",
                timeout_s=120.0,
            )
        if result is not None:
            failures.append(f"{label} -> {result!r}, expected None")
    assert not failures, "; ".join(failures)


def test_surya_config_defaults_are_off_and_local():
    """The fallback ships disabled; the URL and timeout are pinned.

    Asserted twice over: on the live ``settings`` singleton (what production
    reads) and on a freshly loaded copy with the three SURYA_* env vars
    removed (what a clean deployment gets).
    """
    import os

    from pageindex_mcp.config import _load_settings

    expected = {
        "surya_fallback_enabled": False,
        "surya_service_url": "http://localhost:8207",
        "surya_fallback_timeout_s": 120.0,
    }
    env_backup = {
        k: os.environ.pop(k, None)
        for k in ("SURYA_FALLBACK_ENABLED", "SURYA_SERVICE_URL", "SURYA_FALLBACK_TIMEOUT_S")
    }
    try:
        clean = _load_settings()
    finally:
        for k, v in env_backup.items():
            if v is not None:
                os.environ[k] = v

    failures = []
    for source_name, source in (("settings", settings), ("_load_settings()", clean)):
        for field, want in expected.items():
            got = getattr(source, field)
            if got != want:
                failures.append(f"{source_name}.{field} == {got!r}, want {want!r}")
    assert not failures, "; ".join(failures)


def test_density_fallback_is_gated_on_all_five_conjuncts():
    """RFC-047 D8: the Arabic density fallback fires only for a FAIL whose
    reason is suspect_density, on an Arabic-dominant PDF, with the kill switch
    on.

    Read off the production AST rather than re-derived in the test: dropping
    ``ext == ".pdf"`` (Surya's /ocr/pdf would be handed a .docx) or the
    ``surya_fallback_enabled`` kill switch (unbudgeted OCR spend on every
    Arabic density failure) must fail here.
    """
    from pageindex_mcp.client import indexer as indexer_mod

    src = inspect.getsource(indexer_mod)
    gating_conjuncts = None
    for node in ast.walk(ast.parse(src)):
        if not isinstance(node, ast.If):
            continue
        if not (isinstance(node.test, ast.BoolOp) and isinstance(node.test.op, ast.And)):
            continue
        body_src = "\n".join(ast.unparse(stmt) for stmt in node.body)
        if "_surya_density_recovery" not in body_src:
            continue
        gating_conjuncts = {ast.unparse(v) for v in node.test.values}
        break

    assert gating_conjuncts is not None, (
        "no `if <a and b and ...>:` block calling _surya_density_recovery was found in "
        "client/indexer.py — the density fallback gate has moved or been removed"
    )
    expected = {
        "f_verdict == 'FAIL'",
        "'suspect_density' in f_verdict_reason",
        "_is_arabic_dominant",
        "settings.surya_fallback_enabled",
        "ext == '.pdf'",
    }
    assert gating_conjuncts == expected, (
        f"density-fallback gate conjuncts changed:\n"
        f"  missing: {sorted(expected - gating_conjuncts)}\n"
        f"  added:   {sorted(gating_conjuncts - expected)}"
    )


# ===========================================================================
# scripts/ocr_spike_eval.py — RFC-046 Wave 2 / D1 (tasks 2.1-2.4)
#
# A standalone script (not a package module), imported as
# ``scripts.ocr_spike_eval`` (namespace package, repo root on sys.path via
# tests/__init__.py) and mocked at the httpx boundary — no real network calls
# are made anywhere below.
# ===========================================================================

# (runner_name, kwargs, endpoint_attr, input_suffix) — each of the six
# httpx.post call sites named in the task (lines 346, 386, 422, 453, 491, 522).
_RUNNERS = [
    ("run_paddleocr_on_pdf", {"max_pages": 3}, "PADDLEOCR_URL", "doc.pdf"),
    ("run_paddleocr_vl_on_pdf", {"max_pages": 3}, "PADDLEOCR_VL_URL", "doc.pdf"),
    ("run_surya_on_pdf", {"max_pages": 3}, "SURYA_URL", "doc.pdf"),
    ("run_paddleocr_on_image", {}, "PADDLEOCR_URL", "doc.jpg"),
    ("run_paddleocr_vl_on_image", {}, "PADDLEOCR_VL_URL", "doc.jpg"),
    ("run_surya_on_image", {}, "SURYA_URL", "doc.jpg"),
]


def _write_input(tmp_path, name):
    path = tmp_path / name
    path.write_bytes(
        b"%PDF-1.4 fake pdf bytes for open()" if name.endswith(".pdf") else b"\xff\xd8\xff\xe0fake"
    )
    return path


def test_every_runner_raises_when_the_engine_connection_is_refused(tmp_path):
    """A connection failure must propagate, never become a normal-looking
    ``[{"error": ...}]`` result.

    That swallowed shape is what voided the RFC-036 D7 negative result:
    downstream aggregation read "engine down" as "zero chars".
    """
    failures = []
    for fn_name, extra_kwargs, _url_attr, input_name in _RUNNERS:
        path = _write_input(tmp_path, f"{fn_name}-{input_name}")
        fn = getattr(ocr_eval, fn_name)
        with patch("httpx.post", side_effect=httpx.ConnectError("Connection refused")):
            try:
                result = fn(str(path), **extra_kwargs)
            except (TypeError, FileNotFoundError) as exc:
                # Guard against a "pass" that only comes from an unrelated
                # local file/type error rather than the engine-down path.
                failures.append(f"{fn_name}: unrelated {type(exc).__name__}: {exc}")
                continue
            except Exception:
                continue  # raising is the desired behaviour
        failures.append(f"{fn_name}: swallowed the connection failure into {result!r}")
    assert not failures, "\n".join(failures)


class _UnparseableResponse:
    """A 200 whose body is not JSON — a crashed or truncated engine response."""

    status_code = 200

    def raise_for_status(self) -> None:
        return None

    def json(self):
        raise json.JSONDecodeError("Expecting value", "", 0)


def test_every_runner_raises_when_a_reachable_engine_returns_an_unparseable_body(tmp_path):
    """The first round of 2.1 hardened only *unreachable* engines, driving the
    failure with httpx.ConnectError. But ``resp.json()`` sits in the same try
    block and json.JSONDecodeError is a ValueError, not an httpx error — so a
    crashed engine returning a truncated 200, or a proxy answering 200 for a
    dead upstream, escaped EngineUnreachableError and was recorded as
    ``[{"error": ...}]``, exiting 0 with an artifact that looks like a result.
    """
    failures = []
    for fn_name, extra_kwargs, url_attr, input_name in _RUNNERS:
        path = _write_input(tmp_path, f"{fn_name}-bad-{input_name}")
        fn = getattr(ocr_eval, fn_name)
        with patch("httpx.post", return_value=_UnparseableResponse()):
            try:
                result = fn(str(path), **extra_kwargs)
            except ocr_eval.EngineUnreachableError as exc:
                if getattr(ocr_eval, url_attr) not in str(exc):
                    failures.append(f"{fn_name}: message does not name {url_attr}: {exc}")
                continue
            except Exception as exc:
                failures.append(
                    f"{fn_name}: raised {type(exc).__name__}, want EngineUnreachableError"
                )
                continue
        failures.append(f"{fn_name}: returned {result!r} instead of raising")
    assert not failures, "\n".join(failures)


def _main_argv(base, *extra):
    base.mkdir(parents=True, exist_ok=True)
    doc_store = base / "doc_store"
    doc_store.mkdir(exist_ok=True)
    return doc_store, [
        "ocr_spike_eval.py",
        "--doc-store",
        str(doc_store),
        "--out-dir",
        str(base / "out"),
        *extra,
    ]


def test_main_hard_fails_naming_the_engine_and_endpoint_when_a_health_check_is_refused(
    tmp_path, capsys
):
    """main() must exit non-zero, naming the engine and its endpoint, when an
    engine it was NOT told to skip is unreachable."""
    cases = {
        # paddleocr is not skipped; surya is the second health check (line 811)
        "paddleocr": ([], "PADDLEOCR_URL"),
        "surya": (["--skip-paddleocr", "--skip-paddleocr-vl"], "SURYA_URL"),
    }
    failures = []
    for engine, (skips, url_attr) in cases.items():
        _, argv = _main_argv(tmp_path / engine, *skips)
        with (
            patch.object(sys, "argv", argv),
            patch("httpx.get", side_effect=httpx.ConnectError("Connection refused")),
        ):
            try:
                ocr_eval.main()
            except SystemExit as exc:
                if exc.code in (0, None):
                    failures.append(f"{engine}: exit code {exc.code}")
            else:
                failures.append(f"{engine}: main() returned instead of exiting")
                continue
        captured = capsys.readouterr()
        combined = (captured.out + captured.err).lower()
        if engine not in combined or getattr(ocr_eval, url_attr).lower() not in combined:
            failures.append(f"{engine}: output names neither the engine nor {url_attr}")
    assert not failures, "; ".join(failures)


def test_main_completes_when_engines_are_skipped_and_when_a_single_document_is_corrupt(tmp_path):
    """Two boundaries the recon flagged, neither of which is the 2.1 defect:

    (a) ``--skip-paddleocr`` means the user CHOSE to skip it — coercing
        "unreachable" and "user skipped" into one flag is exactly the defect;
    (b) Tesseract's own local per-document failure (a corrupt PDF PyMuPDF
        cannot open) must stay caught, so the rest of the corpus is processed.
    """
    skips = ["--skip-paddleocr", "--skip-paddleocr-vl", "--skip-surya"]
    failures = []
    for label, seed_corrupt in (("all_skipped", False), ("corrupt_document", True)):
        doc_store, argv = _main_argv(tmp_path / label, *skips)
        if seed_corrupt:
            (doc_store / "corrupt.pdf").write_bytes(b"not actually a pdf")
        # httpx.get is patched to raise so an ignored --skip fails loudly.
        with (
            patch.object(sys, "argv", argv),
            patch("httpx.get", side_effect=httpx.ConnectError("refused")),
        ):
            try:
                ocr_eval.main()
            except SystemExit as exc:
                if exc.code not in (0, None):
                    failures.append(f"{label}: main() exited with code {exc.code}")
    assert not failures, "; ".join(failures)


# ---------------------------------------------------------------------------
# 2.2 — the harness must derive Tesseract languages the way production does
# ---------------------------------------------------------------------------

# An Arabic filename long enough that the Latin letters contributed by the
# ".jpg" extension fall below detect_ocr_langs' _MIXED_SCRIPT_MIN_RATIO
# (0.10), so production classifies it as Arabic-only ['ara'], while the
# harness's old private map always appended 'eng'.
_ARABIC_DOMINANT_FILENAME = "شهادة_ميلاد_وثيقة_رسمية_صادرة_من_وزارة_الداخلية_للمواطن.jpg"

_GERMAN_TEXT_SAMPLE = (
    "Allgemeine Versicherungsbedingungen für die Haftpflichtversicherung. "
    "Der Versicherungsschutz erstreckt sich auf gesetzliche Schadenersatzansprüche "
    "und schließt Ansprüche aus Tätigkeiten für Dritte ausdrücklich ein."
)


def test_run_tesseract_on_image_uses_production_language_detection_not_the_private_map(tmp_path):
    """For an Arabic-dominant filename, production's detect_ocr_langs returns
    ['ara'] alone; the deleted private map (_tess_langs_from_detected) always
    appended 'eng'. The langs handed to _tesseract_ocr_image must match
    production, not that map.
    """
    img_path = tmp_path / _ARABIC_DOMINANT_FILENAME
    img_path.write_bytes(b"fake image bytes")
    captured_langs = {}

    def fake_tesseract_ocr_image(path, langs):
        captured_langs["langs"] = list(langs)
        return "بيانات نصية"

    with (
        patch(
            "pageindex_mcp.converters.pictures._tesseract_ocr_image",
            side_effect=fake_tesseract_ocr_image,
        ),
        # ensure_tessdata shells out to `tesseract --list-langs` when
        # TESSDATA_PREFIX is unset, which would make this depend on the host's
        # language packs. Stub it so this pins detect_ocr_langs and nothing else.
        patch.object(ocr_eval, "ensure_tessdata", side_effect=lambda langs: list(langs)),
    ):
        ocr_eval.run_tesseract_on_image(str(img_path), detected_langs=None)

    assert "langs" in captured_langs, "the mocked _tesseract_ocr_image was never called"
    assert captured_langs["langs"] == ["ara"], (
        f"expected production's detect_ocr_langs result ['ara'], got "
        f"{captured_langs['langs']!r} — looks like the private "
        "_tess_langs_from_detected map (which always appends 'eng') is still in use"
    )


def test_select_tesseract_langs_unions_the_text_sample_only_when_there_is_one():
    """Production's contract at pictures.py:1088-1094 is the UNION of the
    filename's languages and the extracted text's — feeding the filename alone
    reproduces half of it and loses 'deu' on German documents behind Latin
    filenames.

    But images.py:134 / indexer.py:893 call detect_ocr_langs(filename) with no
    union, and unioning an ABSENT sample would be worse than useless:
    detect_ocr_langs("") returns ['deu','eng'] as an empty-input fallback, so
    every Arabic-only document would silently acquire German.
    """
    filename = "Policy-Terms-2025.pdf"
    english_sample = "This policy sets out the terms of cover and the claims process."
    with patch.object(ocr_eval, "ensure_tessdata", side_effect=lambda langs: list(langs)):
        with_german = ocr_eval._select_tesseract_langs(filename, _GERMAN_TEXT_SAMPLE)
        with_english = ocr_eval._select_tesseract_langs(filename, english_sample)
        no_sample = ocr_eval._select_tesseract_langs(_ARABIC_DOMINANT_FILENAME)

    # The same filename, two samples: only the sample can explain a difference,
    # so this pins the union rather than the filename's own result.
    assert "deu" not in with_english, (
        f"precondition: an English sample must not yield German, got {with_english!r}"
    )
    assert "deu" in with_german, (
        f"expected the German text sample to contribute 'deu', got {with_german!r} — "
        "the harness is still deriving languages from the filename alone and so "
        "does not measure production's language path"
    )
    assert no_sample == detect_ocr_langs(_ARABIC_DOMINANT_FILENAME)
    assert "deu" not in no_sample, (
        f"got {no_sample!r} — an empty text sample is being unioned in, and its "
        "['deu','eng'] fallback has polluted an Arabic-only selection"
    )


def test_text_layer_detection_returns_the_sample_it_analysed(tmp_path):
    """detect_lang_from_text_layer must hand back the text, not only ISO codes.

    Phase 0 already extracts this text; discarding it is why the language
    union above had nothing to union with.
    """
    fitz = pytest.importorskip("fitz")
    pdf_path = tmp_path / "german.pdf"
    doc = fitz.open()
    doc.new_page().insert_text((72, 72), _GERMAN_TEXT_SAMPLE[:200])
    doc.save(str(pdf_path))
    doc.close()

    info = ocr_eval.detect_lang_from_text_layer(pdf_path)

    assert info.get("text_sample"), (
        f"no text_sample in {sorted(info)} — the extracted text is still being "
        "discarded after language classification"
    )


# ---------------------------------------------------------------------------
# 2.3 — compare_results must label the second engine honestly, and the report
#        consumer must read whatever label it actually uses
# ---------------------------------------------------------------------------

_TESS_PAGES = [{"page_index": 0, "text": "hello", "char_count": 5, "elapsed_s": 1.0}]
_SURYA_PAGES = [
    {
        "page_index": 0,
        "text": "hello world",
        "char_count": 11,
        "elapsed_s": 2.0,
        "confidence": 0.9,
    }
]


def test_compare_results_does_not_label_surya_data_as_paddleocr():
    """compare_results once hardcoded 'paddleocr_*' key names and a literal
    'paddleocr' winner string regardless of which second engine was passed in,
    so doc['comparison_surya']['paddleocr_total_chars'] reported Surya's char
    count under PaddleOCR's name."""
    result = ocr_eval.compare_results(_TESS_PAGES, _SURYA_PAGES)

    offending_keys = [k for k in result if "paddleocr" in k]
    offending_values = [v for v in result.values() if isinstance(v, str) and "paddleocr" in v]
    assert not offending_keys, (
        f"compare_results labelled surya's data with paddleocr-named keys: {offending_keys!r}"
    )
    assert not offending_values, (
        f"compare_results reported a 'paddleocr' winner for a surya comparison: "
        f"{offending_values!r}"
    )


def test_write_human_report_reads_surya_comparison_under_its_own_labels(tmp_path):
    """The line-691 consumer must read the honest key names compare_results now
    emits, rather than falling back to its '-'/0 placeholders."""
    summary = {
        "total_documents": 1,
        "overall": {"paddleocr_wins": 0, "tesseract_wins": 0, "recommendation": "n/a"},
        "pdf_inspector": {},
        "by_language": {},
    }
    comparison_surya_honest = {
        "tesseract_total_chars": 5,
        "other_total_chars": 4242,
        "char_diff": 4237,
        "char_ratio": 848.4,
        "tesseract_total_time_s": 1.0,
        "other_total_time_s": 2.0,
        "other_avg_confidence": 0.9,
        "other_low_conf_pages": 0,
        "winner_by_chars": "other",
        "winner_by_speed": "tesseract",
    }
    doc = {
        "filename": "doc.pdf",
        "detected_langs": ["en"],
        "file_type": "pdf",
        "pdf_inspector": {},
        "comparison": {
            "tesseract_total_chars": 5,
            "paddleocr_total_chars": 5,
            "paddleocr_avg_confidence": 0.9,
            "winner_by_chars": "tie",
        },
        "comparison_vl": {"note": "one or both engines skipped/errored"},
        "comparison_surya": comparison_surya_honest,
    }
    out_path = tmp_path / "eval_report.md"

    ocr_eval.write_human_report(summary, [doc], out_path)
    report_text = out_path.read_text(encoding="utf-8")

    assert "4242" in report_text, (
        "write_human_report did not surface Surya's char count from the "
        "corrected (non-paddleocr-labelled) comparison_surya keys — the "
        "consumer at line 691 still reads the old paddleocr_* names"
    )


# ---------------------------------------------------------------------------
# 2.4 — the existing eval_report.md must carry an unverified-numbers header
# ---------------------------------------------------------------------------


def test_eval_report_has_an_unverified_numbers_header():
    """eval_report.md must state measurement caveats (yield != accuracy)."""
    text = Path("agents/spikes/ocr_eval_rfc046/eval_report.md").read_text(encoding="utf-8")
    head = text[:2000].lower()

    assert "character yield" in head or "not accuracy" in head, (
        "eval_report.md must caveat that it measures character yield, not accuracy"
    )
