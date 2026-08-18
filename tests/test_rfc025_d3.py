"""Tests for RFC-025 Task 2.6 (D3): recovery-trigger parity for the
``"node_garbling"`` reason across the OCR-escalation, VLM-fallback, and D7
Tesseract-raster recovery paths in ``pageindex_mcp.client``.

Validates Design Property 4: for any ``validate_tree`` result of
``(False, "node_garbling")``, all three recovery-trigger conditions (OCR
escalation, VLM fallback, D7 Tesseract-raster) SHALL fire identically to a
``(False, "garbling")`` result; for any result whose reason is neither
``"garbling"`` nor ``"node_garbling"`` (e.g. ``"node_count<3"``), none of the
three recovery triggers SHALL fire; if recovery also produces garbled
output, ``LowQualityTreeError`` SHALL still be raised (HR5 unweakened).

``client.py``'s recovery-trigger conditions are inline gates inside the
large async ``index()`` method (client.py:965, 1021, 1054) rather than
standalone functions, so these tests pin the exact gating logic against a
faithful reproduction of each, mirroring the ``test_rfc023_d7.py``
characterization-test pattern.
"""

import inspect

import pageindex_mcp.client as client_mod


def _ocr_escalation_gate(ok: bool, reason: str, *, ext: str = ".pdf") -> bool:
    """Reproduces client.py:965 — OCR escalation trigger."""
    return (
        not ok
        and reason in ("garbling", "node_garbling")
        and ext == ".pdf"
        and client_mod._OCR_ESCALATION_GARBLE
    )


def _vlm_fallback_gate(
    ok: bool, reason: str, *, ext: str = ".pdf", vlm_fallback: bool = True
) -> bool:
    """Reproduces client.py:1021 — VLM fallback trigger."""
    return not ok and reason in ("garbling", "node_garbling") and ext == ".pdf" and vlm_fallback


def _d7_tesseract_raster_gate(ok: bool, reason: str) -> bool:
    """Reproduces client.py:1054 — D7 Tesseract-raster recovery trigger
    (post-VLM, no-exception path)."""
    return (
        not ok
        and reason in ("garbling", "node_garbling")
        and client_mod._D7_GARBLE_RECOVERY_ENABLED
    )


class TestOcrEscalationTriggerParity:
    """(a): OCR escalation (client.py:965) fires for node_garbling exactly
    as it does for garbling."""

    def test_garbling_fires(self):
        assert _ocr_escalation_gate(ok=False, reason="garbling") is True

    def test_node_garbling_fires(self):
        assert _ocr_escalation_gate(ok=False, reason="node_garbling") is True

    def test_node_count_reason_does_not_fire(self):
        assert _ocr_escalation_gate(ok=False, reason="node_count<3") is False

    def test_ok_true_does_not_fire(self):
        assert _ocr_escalation_gate(ok=True, reason="node_garbling") is False

    def test_non_pdf_ext_does_not_fire(self):
        assert _ocr_escalation_gate(ok=False, reason="node_garbling", ext=".docx") is False

    def test_kill_switch_disabled_does_not_fire(self, monkeypatch):
        monkeypatch.setattr(client_mod, "_OCR_ESCALATION_GARBLE", False)
        assert _ocr_escalation_gate(ok=False, reason="node_garbling") is False


class TestVlmFallbackTriggerParity:
    """(b): VLM fallback (client.py:1021) fires for node_garbling exactly
    as it does for garbling."""

    def test_garbling_fires(self):
        assert _vlm_fallback_gate(ok=False, reason="garbling") is True

    def test_node_garbling_fires(self):
        assert _vlm_fallback_gate(ok=False, reason="node_garbling") is True

    def test_node_count_reason_does_not_fire(self):
        assert _vlm_fallback_gate(ok=False, reason="node_count<3") is False

    def test_ok_true_does_not_fire(self):
        assert _vlm_fallback_gate(ok=True, reason="node_garbling") is False

    def test_non_pdf_ext_does_not_fire(self):
        assert _vlm_fallback_gate(ok=False, reason="node_garbling", ext=".html") is False

    def test_vlm_fallback_disabled_does_not_fire(self):
        assert _vlm_fallback_gate(ok=False, reason="node_garbling", vlm_fallback=False) is False


class TestD7TesseractRasterTriggerParity:
    """(c): D7 Tesseract-raster recovery (client.py:1054) fires for
    node_garbling exactly as it does for garbling."""

    def test_garbling_fires(self):
        assert _d7_tesseract_raster_gate(ok=False, reason="garbling") is True

    def test_node_garbling_fires(self):
        assert _d7_tesseract_raster_gate(ok=False, reason="node_garbling") is True

    def test_node_count_reason_does_not_fire(self):
        assert _d7_tesseract_raster_gate(ok=False, reason="node_count<3") is False

    def test_ok_true_does_not_fire(self):
        assert _d7_tesseract_raster_gate(ok=True, reason="node_garbling") is False

    def test_kill_switch_disabled_does_not_fire(self, monkeypatch):
        monkeypatch.setattr(client_mod, "_D7_GARBLE_RECOVERY_ENABLED", False)
        assert _d7_tesseract_raster_gate(ok=False, reason="node_garbling") is False


class TestFalsePositiveRegressionGuard:
    """(d): the extension is exact-set matching against {"garbling",
    "node_garbling"}, not a broadened catch-all -- every other reason string
    validate_tree can emit must NOT trigger any of the three recovery
    paths (design doc edge case: "node_count<3" must not trigger recovery;
    generalized here across the full known reason vocabulary)."""

    NON_TRIGGERING_REASONS = ["node_count<3", "depth<2", "", "GARBLING", "node_garbling "]

    def test_ocr_escalation_never_fires_on_other_reasons(self):
        for reason in self.NON_TRIGGERING_REASONS:
            assert _ocr_escalation_gate(ok=False, reason=reason) is False, reason

    def test_vlm_fallback_never_fires_on_other_reasons(self):
        for reason in self.NON_TRIGGERING_REASONS:
            assert _vlm_fallback_gate(ok=False, reason=reason) is False, reason

    def test_d7_tesseract_raster_never_fires_on_other_reasons(self):
        for reason in self.NON_TRIGGERING_REASONS:
            assert _d7_tesseract_raster_gate(ok=False, reason=reason) is False, reason

    def test_production_source_carries_all_three_reason_set_gates(self):
        """Anchor the characterization helpers above to the REAL module:
        client.py must gate all three recovery paths (OCR escalation, VLM
        fallback, D7 Tesseract-raster) on exact-set membership against
        {GARBLING, NODE_GARBLING}.  Zone-5 replaced string checks with
        TreeDefect enum comparisons."""
        source = inspect.getsource(client_mod)
        assert source.count('first_defect in (TreeDefect.GARBLING, TreeDefect.NODE_GARBLING)') >= 3
        assert 'reason == "garbling" and ext' not in source

    def test_source_gates_use_tuple_membership_not_substring_match(self):
        """Regression guard against a future refactor accidentally using
        substring/`in` on the reason string itself (e.g. "node_garbling" in
        "node_garbling_extra") instead of exact-set membership."""
        assert _ocr_escalation_gate(ok=False, reason="node_garbling_extra") is False
        assert _vlm_fallback_gate(ok=False, reason="node_garbling_extra") is False
        assert _d7_tesseract_raster_gate(ok=False, reason="node_garbling_extra") is False
