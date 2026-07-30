"""Tests for RFC-023 Task 4.2 (D7): Tesseract-on-raster fallback when the VLM
crashes on garbled PDFs.

Validates Design Property 8: for any VLM exception raised during vision
fallback, the system SHALL run Tesseract OCR on the rasterized page images;
if the resulting OCR text passes ``_is_garbled_blob`` (returns ``False``), the
system SHALL use it as ``flat_md`` and override ``reason`` to
``'node_count<3'`` to enter the flat success path; if the OCR text is
garbled or empty, the system SHALL still raise ``LowQualityTreeError``
('garbling').

``client.py``'s VLM-exception handler (~line 902-937) is inline inside the
large async ``index()`` method rather than a standalone function, so these
tests pin the exact gating logic -- the ``_VLM_TESSERACT_FALLBACK_ENABLED``
kill switch and the reason-override-on-recovery-only invariant -- against a
faithful reproduction of client.py:916-930, mirroring the
``test_rfc023_d11.py`` characterization-test pattern.

**RFC-024 D5 supersession note (case (d)):** the original RFC-023 D7 test
spec's case (d) -- "garbling reason without VLM exception: existing
escalation path unchanged" -- asserted that a ``'garbling'`` reason reached
via the VLM *try*-block (no exception) fell straight through to
``LowQualityTreeError`` unchanged. RFC-024 D5 inverts this: that path now
invokes ``_attempt_tesseract_raster_recovery`` (client.py:948-958). The new
``TestGarblingWithoutVlmExceptionInvokesRecovery`` class below asserts the
superseding behavior; the original assertion is preserved verbatim in
``TestGarblingWithoutVlmExceptionKillSwitch`` as a regression test gated on
``D7_GARBLE_RECOVERY_ENABLED=false``.
"""

import base64

import pageindex_mcp.client as client_mod
from pageindex_mcp import converters
from pageindex_mcp.client import _VLM_TESSERACT_FALLBACK_ENABLED
from pageindex_mcp.helpers import _flat_text_is_garbled

_FITZ_PNG = f"data:image/png;base64,{base64.b64encode(b'FITZ_PNG_FAKE').decode()}"

_GARBLED_TEXT = " ".join(["xkjqz"] * 40)
_CLEAN_TEXT = "This is a perfectly ordinary page of legible English prose. " * 3


def _vlm_tesseract_fallback(ocr_text: str, *, reason: str = "garbling") -> str:
    """Reproduces client.py:916-930's recovery/reason-override logic exactly."""
    if ocr_text and not _flat_text_is_garbled(ocr_text):
        reason = "node_count<3"
    return reason


class TestVlmTesseractFallback:
    def test_clean_ocr_text_overrides_reason_to_node_count(self):
        """Non-garbled Tesseract-on-raster recovery flips reason from
        'garbling' to 'node_count<3' so the flat success path is entered."""
        assert _vlm_tesseract_fallback(_CLEAN_TEXT) == "node_count<3"

    def test_garbled_ocr_text_leaves_reason_as_garbling(self):
        """Garbled Tesseract output must NOT override the reason -- the
        document still raises LowQualityTreeError('garbling') per HR5."""
        assert _vlm_tesseract_fallback(_GARBLED_TEXT) == "garbling"

    def test_empty_ocr_text_leaves_reason_as_garbling(self):
        """Empty Tesseract output (OCR found nothing) must not override the
        reason -- an empty string never enters the flat success path."""
        assert _vlm_tesseract_fallback("") == "garbling"

    def test_reason_never_becomes_garbling_itself(self):
        """The override target is always 'node_count<3', never 'garbling' --
        'garbling' must never become a flat-routable reason (invariant
        preserved from the Reason-Override design decision)."""
        result = _vlm_tesseract_fallback(_CLEAN_TEXT)
        assert result != "garbling"

    def test_kill_switch_env_var_defaults_true(self):
        """VLM_TESSERACT_FALLBACK_ENABLED defaults to true (feature is
        enabled out of the box)."""
        assert _VLM_TESSERACT_FALLBACK_ENABLED is True


def _garbling_without_exception_gate(ok: bool, reason: str) -> bool:
    """Reproduces client.py:948's RFC-024 D5 gate: after the VLM try-block's
    validate_tree() call succeeds (no exception raised), recovery fires only
    when ok is False, reason is 'garbling', and D7_GARBLE_RECOVERY_ENABLED."""
    return not ok and reason == "garbling" and client_mod._D7_GARBLE_RECOVERY_ENABLED


class TestGarblingWithoutVlmExceptionInvokesRecovery:
    """RFC-024 D5 supersedes the original case (d): a 'garbling' reason
    reached without a VLM exception now triggers Tesseract-on-raster
    recovery instead of falling straight through to LowQualityTreeError."""

    def test_garbling_without_exception_invokes_recovery(self):
        assert _garbling_without_exception_gate(ok=False, reason="garbling") is True

    def test_non_garbling_reason_without_exception_does_not_invoke_recovery(self):
        assert _garbling_without_exception_gate(ok=False, reason="node_count<3") is False

    def test_ok_true_does_not_invoke_recovery(self):
        assert _garbling_without_exception_gate(ok=True, reason="") is False


class TestGarblingWithoutVlmExceptionKillSwitch:
    """Regression test preserving the ORIGINAL RFC-023 D7 case (d) assertion
    -- 'garbling' reason without a VLM exception falls through to
    LowQualityTreeError unchanged -- now scoped to the
    D7_GARBLE_RECOVERY_ENABLED=false rollback path (RFC-024 D5 Rollback)."""

    def test_kill_switch_disabled_preserves_original_fall_through_behavior(self, monkeypatch):
        monkeypatch.setattr(client_mod, "_D7_GARBLE_RECOVERY_ENABLED", False)
        assert _garbling_without_exception_gate(ok=False, reason="garbling") is False


class TestFitzRasterizationPathCoverage:
    """RFC-024 D4 extension: tesseract_ocr_pdf_pages' pypdfium2-first,
    fitz-fallback rasterization path, exercised from the D7 test suite."""

    async def test_pypdfium2_failure_falls_back_to_fitz_success(self, monkeypatch):
        def _pdfium_boom(pdf_path, dpi=200):
            raise RuntimeError("CMap corruption: pypdfium2 render failed")

        monkeypatch.setattr(converters, "rasterize_pdf_pages", _pdfium_boom)
        monkeypatch.setattr(
            converters,
            "rasterize_pdf_pages_fitz",
            lambda pdf_path, dpi=200: [_FITZ_PNG],
        )
        monkeypatch.setattr(converters, "_tesseract_ocr_image", lambda path, langs: _CLEAN_TEXT)

        result = await converters.tesseract_ocr_pdf_pages("/fake.pdf", ["eng"])

        assert result == _CLEAN_TEXT
        assert not _flat_text_is_garbled(result)
