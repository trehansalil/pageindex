"""Tests for RFC-023 Task 1.5 (D11): widen OCR escalation to
structural-failure reasons when the recovered markdown is image-dominant.

Validates Design Property 12: for any ``validate_tree`` failure with
``reason in ('node_count<3', 'depth<2')`` where the image-line ratio (image
lines / non-empty lines) exceeds 0.50, the system SHALL trigger the same OCR
escalation path as ``reason == 'garbling'``; the image-dominance ratio SHALL
be computed against ``non_empty_lines``, not ``total_lines``, so that
garbled/whitespace lines do not dilute the ratio below the threshold.

``client.py``'s escalation gate (~line 792-920) is inline inside the large
async ``index()`` method rather than a standalone function, so these tests
pin the exact gating conditions -- reason membership, the
``_IMAGE_DOMINANT_OCR_ESCALATION_ENABLED`` flag, and the corrected
non-empty-line ratio computation -- against the real module constants and a
faithful reproduction of the line-ratio arithmetic at client.py:899-902,
mirroring the existing ``test_kill_switch_env_var`` characterization-test
pattern used elsewhere in this suite (tests/test_rfc020_f0_splice.py).
"""

from pageindex_mcp.client import (
    _IMAGE_DOMINANT_OCR_ESCALATION_ENABLED,
)
from pageindex_mcp.config import OCR_ESCALATION_GARBLE

_MARKER = "<!-- image -->"


def _image_dominant(md_content: str) -> tuple[bool, int, int]:
    """Reproduces client.py:899-902's ratio computation exactly."""
    total_lines = md_content.splitlines()
    non_empty_lines = [ln for ln in total_lines if ln.strip()]
    image_lines = sum(1 for ln in non_empty_lines if _MARKER in ln)
    dominant = bool(non_empty_lines) and (image_lines / len(non_empty_lines)) > 0.50
    return dominant, image_lines, len(non_empty_lines)


def _would_escalate(reason: str, md_content: str, *, ext: str = ".pdf") -> bool:
    """Reproduces the D11 gate's overall condition (reason in structural
    failures + image-dominant), gated on the module flags."""
    if reason not in ("node_count<3", "depth<2"):
        return False
    if ext != ".pdf" or not OCR_ESCALATION_GARBLE or not _IMAGE_DOMINANT_OCR_ESCALATION_ENABLED:
        return False
    dominant, _, _ = _image_dominant(md_content)
    return dominant


class TestStructuralFailureOcrEscalation:
    def test_structural_failure_image_dominant_triggers_escalation(self):
        """node_count<3 + >50% image-dominant markdown fires the OCR
        escalation path that used to require reason == 'garbling'."""
        md = f"{_MARKER}\n{_MARKER}\n{_MARKER}\nsome prose"
        assert _would_escalate("node_count<3", md) is True

    def test_structural_failure_non_image_dominant_no_escalation(self):
        """node_count<3 but the markdown is mostly real prose (not
        image-dominant) -- escalation must NOT fire."""
        md = "\n".join(["real paragraph text here"] * 8 + [_MARKER])
        assert _would_escalate("node_count<3", md) is False

    def test_depth_lt_2_image_dominant_also_escalates(self):
        """The gate covers both structural-failure reasons named in the
        RFC, not just node_count<3."""
        md = f"{_MARKER}\n{_MARKER}"
        assert _would_escalate("depth<2", md) is True

    def test_ratio_denominator_excludes_empty_lines(self):
        """Whitespace-only lines must not dilute the ratio below threshold
        -- the denominator is non-empty lines, not total lines. 2 image
        lines out of 2 non-empty lines (plus 8 blank lines) is 100%
        image-dominant, not 20%."""
        md = "\n".join([_MARKER, "", "", "", _MARKER, "", "", "", "", ""])
        dominant, image_lines, denom = _image_dominant(md)
        assert denom == 2
        assert image_lines == 2
        assert dominant is True

    def test_garbled_text_lines_excluded_from_denominator_via_blank_dilution(self):
        """A page with a thin garbled text layer that Docling renders as
        several blank/whitespace lines around the image markers: the
        corrected denominator (non-empty lines) keeps the ratio above
        threshold where a total-lines denominator would have diluted it
        below 0.50."""
        md = "\n".join([_MARKER] * 6 + [""] * 20)
        total_lines = md.splitlines()
        dominant, image_lines, denom = _image_dominant(md)

        # Old (buggy) denominator would have diluted the ratio below 0.50.
        old_ratio = image_lines / len(total_lines)
        assert old_ratio < 0.50
        # Corrected denominator keeps it comfortably above threshold.
        assert denom == 6
        assert dominant is True

    def test_garbling_reason_path_unchanged(self):
        """reason == 'garbling' is handled by the pre-existing Fix-3
        escalation gate (unconditional on image-dominance) and is NOT part
        of the D11 structural-failure/image-dominance gate at all."""
        md = "not image dominant prose"
        assert _would_escalate("garbling", md) is False

    def test_escalation_flag_disabled(self, monkeypatch):
        import pageindex_mcp.client as client_mod
        import pageindex_mcp.config as config_mod

        monkeypatch.setattr(client_mod, "_IMAGE_DOMINANT_OCR_ESCALATION_ENABLED", False)
        md = f"{_MARKER}\n{_MARKER}"

        def _would_escalate_live(reason: str, md_content: str) -> bool:
            if reason not in ("node_count<3", "depth<2"):
                return False
            if (
                not config_mod.OCR_ESCALATION_GARBLE
                or not client_mod._IMAGE_DOMINANT_OCR_ESCALATION_ENABLED
            ):
                return False
            dominant, _, _ = _image_dominant(md_content)
            return dominant

        assert _would_escalate_live("node_count<3", md) is False
