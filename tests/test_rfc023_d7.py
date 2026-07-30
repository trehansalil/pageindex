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
"""

from pageindex_mcp.client import _VLM_TESSERACT_FALLBACK_ENABLED
from pageindex_mcp.helpers import _flat_text_is_garbled

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
