"""Unit tests for RFC-032 D1 — the ``inspector_force_ocr`` decision matrix in
``client.py::index()``.

Covers Task 5.1 (RFC-032 D4): flag on/off, each ``pdf_type``, confidence
above/below the 0.90 threshold, and classification absent (``None``), plus
Task 5.3: the same decision reaching the remote Docling route.
"""

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import pageindex_mcp.client as client_mod
from pageindex_mcp.client import CustomPageIndexClient


def _fake_settings(**overrides):
    base = {
        "openai_api_key": "test-key",
        "openai_base_url": "https://api.openai.com/v1",
        "azure_api_version": None,
        "llm_model": "gpt-test",
        "minio_secure": False,
        "minio_endpoint": "localhost:9000",
        "minio_bucket": "pageindex",
        "flat_doc_routing": True,
        "vlm_fallback": False,
        "vlm_model": "gpt-4.1",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.fixture
def pdf_file(tmp_path):
    path = tmp_path / "doc.pdf"
    path.write_bytes(b"%PDF-1.4\n fake pdf bytes")
    return str(path)


def _wire_index(monkeypatch, *, preclassify, validate_tree=None):
    """Patch every collaborator client.index() touches on the PDF -> markdown
    route, and capture the args the (single) converter chain entry is called
    with — so we can inspect whether OCR was forced."""
    monkeypatch.setattr(client_mod, "settings", _fake_settings())
    monkeypatch.setattr(client_mod, "PDF_INSPECTOR_PRECLASSIFY", preclassify)
    monkeypatch.setattr(client_mod, "hash_cache_get", lambda filename: None)
    monkeypatch.setattr(client_mod, "list_processed_docs", lambda: [])
    monkeypatch.setattr(client_mod, "hash_cache_set", MagicMock())
    if validate_tree is None:
        validate_tree = lambda structure, **kw: (True, None)  # noqa: E731
    monkeypatch.setattr(client_mod, "validate_tree", validate_tree)
    monkeypatch.setattr(client_mod, "prepare_tree", lambda structure: structure)

    conv_fn = MagicMock(return_value="# Heading\n\nBody text\n")
    monkeypatch.setattr(client_mod, "pdf_markdown_converters", lambda: [("docling", conv_fn)])
    # Fix-3's OCR-escalation retry calls pdf_to_markdown_docling directly
    # (not the chain entry above) — stub it so a garbling verdict can drive
    # the retry path without touching a real Docling conversion.
    monkeypatch.setattr(
        client_mod,
        "pdf_to_markdown_docling",
        MagicMock(return_value="# Heading\n\nRecovered text\n"),
    )
    # The retry path also calls ensure_tessdata() before OCR — stub it so tests
    # never probe the real tessdata dir (or download traineddata when
    # TESSDATA_ALLOW_DOWNLOAD=1 is set in the environment).
    monkeypatch.setattr(client_mod, "ensure_tessdata", lambda langs: langs)

    mocks = {
        "save_doc": MagicMock(),
        "save_flat_doc": MagicMock(),
        "save_raw": MagicMock(),
        "save_doc_meta": MagicMock(),
        "route_and_extract_flat": MagicMock(
            return_value=("flat_prose", [{"role": "prose", "text": "x"}])
        ),
        "FLAT_DOCS_TOTAL": MagicMock(),
        "LOW_QUALITY_TREES": MagicMock(),
        "OCR_ESCALATION_TOTAL": MagicMock(),
        "VLM_FALLBACK_TOTAL": MagicMock(),
        "RAW_UPLOAD_FAILURES": MagicMock(),
        "PDF_PRIMARY_CONVERTER_FAILURES": MagicMock(),
        "PDF_EXTRACT_FALLBACKS": MagicMock(),
        "PDF_INSPECTOR_FORCED_OCR": MagicMock(),
    }
    for name, m in mocks.items():
        monkeypatch.setattr(client_mod, name, m)
    mocks["conv_fn"] = conv_fn
    return mocks


def _make_client():
    return CustomPageIndexClient(api_key="test-key")


async def _run_index(monkeypatch, pdf_file, *, preclassify, pdf_classification, validate_tree=None):
    mocks = _wire_index(monkeypatch, preclassify=preclassify, validate_tree=validate_tree)
    c = _make_client()
    monkeypatch.setattr(
        c, "_run_md_to_tree", AsyncMock(return_value={"structure": [], "doc_description": "ok"})
    )
    await c.index(pdf_file, pdf_classification=pdf_classification)
    return mocks


class TestInspectorForceOcrDecisionMatrix:
    async def test_forces_ocr_for_scanned_at_high_confidence(self, monkeypatch, pdf_file):
        mocks = await _run_index(
            monkeypatch,
            pdf_file,
            preclassify=True,
            pdf_classification={"pdf_type": "scanned", "confidence": 0.95},
        )

        mocks["conv_fn"].assert_called_once()
        assert mocks["conv_fn"].call_args.args == (pdf_file, True)
        assert "ocr_lang_override" in mocks["conv_fn"].call_args.kwargs
        mocks["PDF_INSPECTOR_FORCED_OCR"].inc.assert_called_once()

    async def test_forces_ocr_for_image_based_at_high_confidence(self, monkeypatch, pdf_file):
        mocks = await _run_index(
            monkeypatch,
            pdf_file,
            preclassify=True,
            pdf_classification={"pdf_type": "image_based", "confidence": 0.92},
        )

        assert mocks["conv_fn"].call_args.args[1] is True
        mocks["PDF_INSPECTOR_FORCED_OCR"].inc.assert_called_once()

    async def test_confidence_below_threshold_falls_through_to_normal_path(
        self, monkeypatch, pdf_file
    ):
        mocks = await _run_index(
            monkeypatch,
            pdf_file,
            preclassify=True,
            pdf_classification={"pdf_type": "scanned", "confidence": 0.85},
        )

        mocks["conv_fn"].assert_called_once_with(pdf_file)
        mocks["PDF_INSPECTOR_FORCED_OCR"].inc.assert_not_called()

    async def test_confidence_exactly_at_threshold_forces_ocr(self, monkeypatch, pdf_file):
        """The gate is ``>= 0.90``, so 0.90 itself must be admitted (RFC-032 D1)."""
        mocks = await _run_index(
            monkeypatch,
            pdf_file,
            preclassify=True,
            pdf_classification={"pdf_type": "scanned", "confidence": 0.90},
        )

        assert mocks["conv_fn"].call_args.args[1] is True
        mocks["PDF_INSPECTOR_FORCED_OCR"].inc.assert_called_once()

    async def test_missing_confidence_key_does_not_force_ocr(self, monkeypatch, pdf_file):
        """A classification dict without ``confidence`` defaults to 0 → no force."""
        mocks = await _run_index(
            monkeypatch,
            pdf_file,
            preclassify=True,
            pdf_classification={"pdf_type": "scanned"},
        )

        mocks["conv_fn"].assert_called_once_with(pdf_file)
        mocks["PDF_INSPECTOR_FORCED_OCR"].inc.assert_not_called()

    async def test_text_based_never_forces_ocr(self, monkeypatch, pdf_file):
        mocks = await _run_index(
            monkeypatch,
            pdf_file,
            preclassify=True,
            pdf_classification={"pdf_type": "text_based", "confidence": 1.0},
        )

        mocks["conv_fn"].assert_called_once_with(pdf_file)
        mocks["PDF_INSPECTOR_FORCED_OCR"].inc.assert_not_called()

    async def test_preclassify_flag_off_ignores_classification(self, monkeypatch, pdf_file):
        mocks = await _run_index(
            monkeypatch,
            pdf_file,
            preclassify=False,
            pdf_classification={"pdf_type": "scanned", "confidence": 1.0},
        )

        mocks["conv_fn"].assert_called_once_with(pdf_file)
        mocks["PDF_INSPECTOR_FORCED_OCR"].inc.assert_not_called()

    async def test_classification_none_preserves_normal_behavior(self, monkeypatch, pdf_file):
        mocks = await _run_index(monkeypatch, pdf_file, preclassify=True, pdf_classification=None)

        mocks["conv_fn"].assert_called_once_with(pdf_file)
        mocks["PDF_INSPECTOR_FORCED_OCR"].inc.assert_not_called()


class TestSafetyNetsIntactAfterInspectorForcedOcr:
    """Task 5.2 (RFC-032 D4): validate_tree() and the Fix-3 OCR-retry escalation
    are unconditional safety nets — they must still run/fire exactly as before
    even when pdf-inspector already forced full-page OCR on the first pass."""

    async def test_fix3_retry_fires_after_forced_ocr_when_validate_tree_flags_garbling(
        self, monkeypatch, pdf_file
    ):
        validate = MagicMock(side_effect=[(False, "garbling"), (True, None)])
        mocks = await _run_index(
            monkeypatch,
            pdf_file,
            preclassify=True,
            pdf_classification={"pdf_type": "scanned", "confidence": 0.95},
            validate_tree=validate,
        )

        assert validate.call_count == 2
        mocks["PDF_INSPECTOR_FORCED_OCR"].inc.assert_called_once()
        mocks["OCR_ESCALATION_TOTAL"].labels.assert_called_once_with(result="recovered")
        mocks["save_doc"].assert_called_once()
        mocks["save_flat_doc"].assert_not_called()

    async def test_no_retry_when_validate_tree_passes_after_forced_ocr(self, monkeypatch, pdf_file):
        validate = MagicMock(return_value=(True, None))
        mocks = await _run_index(
            monkeypatch,
            pdf_file,
            preclassify=True,
            pdf_classification={"pdf_type": "scanned", "confidence": 0.95},
            validate_tree=validate,
        )

        assert validate.call_count == 1
        mocks["PDF_INSPECTOR_FORCED_OCR"].inc.assert_called_once()
        mocks["OCR_ESCALATION_TOTAL"].labels.assert_not_called()
        mocks["save_doc"].assert_called_once()
        mocks["save_flat_doc"].assert_not_called()


class TestInspectorForcedOcrOnRemoteDoclingRoute:
    """Task 5.3 (RFC-032 D4 / Design AD3) — remote-path counterpart of the
    decision matrix above.

    Task 3.1 wired ``inspector_force_ocr`` into BOTH converter-loop branches,
    but the tests above only exercise the local ``conv_fn`` branch
    (``_use_remote`` is False because ``_fake_settings()`` has no
    ``docling_service_url``). These cover the remote
    ``_remote_pdf_to_markdown()`` branch, proving the D2 wiring is symmetric."""

    @staticmethod
    def _wire_remote(monkeypatch, *, preclassify=True):
        mocks = _wire_index(monkeypatch, preclassify=preclassify)
        monkeypatch.setattr(
            client_mod,
            "settings",
            _fake_settings(docling_service_url="http://docling.test"),
        )
        remote = AsyncMock(return_value=("# Heading\n\nBody text\n", []))
        monkeypatch.setattr(client_mod, "_remote_pdf_to_markdown", remote)
        mocks["remote"] = remote
        return mocks

    async def _run(self, monkeypatch, pdf_file, *, pdf_classification, preclassify=True):
        mocks = self._wire_remote(monkeypatch, preclassify=preclassify)
        c = _make_client()
        c._staging_key = "uploads/doc.pdf"
        monkeypatch.setattr(
            c, "_run_md_to_tree", AsyncMock(return_value={"structure": [], "doc_description": "ok"})
        )
        await c.index(pdf_file, pdf_classification=pdf_classification)
        return mocks

    async def test_remote_route_forces_full_page_ocr_for_scanned(self, monkeypatch, pdf_file):
        mocks = await self._run(
            monkeypatch, pdf_file, pdf_classification={"pdf_type": "scanned", "confidence": 0.95}
        )

        mocks["remote"].assert_awaited_once()
        assert mocks["remote"].await_args.kwargs["force_full_page_ocr"] is True
        assert mocks["remote"].await_args.kwargs["ocr_lang_override"]
        mocks["conv_fn"].assert_not_called()
        mocks["PDF_INSPECTOR_FORCED_OCR"].inc.assert_called_once()

    async def test_remote_route_does_not_force_ocr_below_confidence_threshold(
        self, monkeypatch, pdf_file
    ):
        mocks = await self._run(
            monkeypatch, pdf_file, pdf_classification={"pdf_type": "scanned", "confidence": 0.80}
        )

        mocks["remote"].assert_awaited_once()
        assert "force_full_page_ocr" not in mocks["remote"].await_args.kwargs
        mocks["PDF_INSPECTOR_FORCED_OCR"].inc.assert_not_called()

    async def test_remote_route_does_not_force_ocr_for_text_based(self, monkeypatch, pdf_file):
        mocks = await self._run(
            monkeypatch, pdf_file, pdf_classification={"pdf_type": "text_based", "confidence": 0.99}
        )

        mocks["remote"].assert_awaited_once()
        assert "force_full_page_ocr" not in mocks["remote"].await_args.kwargs
        mocks["PDF_INSPECTOR_FORCED_OCR"].inc.assert_not_called()
