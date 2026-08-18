"""Tests for RFC-027 Task 2.3 (D2): low-content OCR escalation for `.pdf`
documents rejected as ``node_count<3`` with fewer than
``LOW_CONTENT_OCR_CHAR_FLOOR`` chars.

``client.py``'s escalation gate is an inline condition inside the large
async ``index()`` method (client.py:~987), not a standalone function, so
these tests pin the exact gating logic against a faithful reproduction of
it, mirroring the ``test_rfc025_d3.py`` characterization-test pattern.
``validate_tree`` returns a 2-tuple ``(bool, str)`` per ``helpers.py:1047``
-- ``total_chars``/``node_count`` come from the structure object, not from
``validate_tree``'s return value.
"""

import pageindex_mcp.client as client_mod


def _escalation_fires(ok: bool, reason: str, total_chars: int, ext: str = ".pdf") -> bool:
    """Reproduces client.py:~987-991 -- the OCR-escalation trigger,
    including the RFC-027 D2 low-content branch."""
    low_content_ocr_eligible = (
        reason == "node_count<3" and total_chars < client_mod.LOW_CONTENT_OCR_CHAR_FLOOR
    )
    return (
        not ok
        and (reason in ("garbling", "node_garbling") or low_content_ocr_eligible)
        and ext == ".pdf"
        and client_mod._OCR_ESCALATION_GARBLE
    )


class TestLowContentOcrEscalationBoundaries:
    def test_zero_chars_zero_nodes_fires(self):
        """(a): a fully empty structure (MOU MOHRE-style) escalates."""
        assert _escalation_fires(ok=False, reason="node_count<3", total_chars=0) is True

    def test_38_chars_fires(self):
        """(b): مرسوم (13) 2022-style near-zero content escalates."""
        assert _escalation_fires(ok=False, reason="node_count<3", total_chars=38) is True

    def test_230_chars_fires(self):
        """(c): القرار التنظيمي-style garbled-but-nonzero content escalates."""
        assert _escalation_fires(ok=False, reason="node_count<3", total_chars=230) is True

    def test_299_chars_fires(self):
        """(d): boundary, just under the 300-char floor -- escalates."""
        assert _escalation_fires(ok=False, reason="node_count<3", total_chars=299) is True

    def test_300_chars_does_not_fire(self):
        """(e): boundary, at the 300-char floor -- floor is exclusive-below,
        so 300 chars does NOT escalate."""
        assert _escalation_fires(ok=False, reason="node_count<3", total_chars=300) is False

    def test_non_pdf_extension_does_not_fire(self):
        """(f): the same low-content case on a non-.pdf extension must not
        escalate -- gate is strictly .pdf-scoped."""
        assert (
            _escalation_fires(ok=False, reason="node_count<3", total_chars=38, ext=".docx") is False
        )

    def test_garbling_reason_unaffected_by_new_branch(self):
        """(g): the pre-existing garbling branch still fires regardless of
        total_chars, confirming the new low-content branch is additive."""
        assert _escalation_fires(ok=False, reason="garbling", total_chars=100000) is True

    def test_node_garbling_reason_unaffected(self):
        assert _escalation_fires(ok=False, reason="node_garbling", total_chars=100000) is True

    def test_ok_true_never_fires(self):
        assert _escalation_fires(ok=True, reason="node_count<3", total_chars=0) is False

    def test_other_reason_below_floor_does_not_fire(self):
        """Only ``node_count<3`` is eligible for the low-content branch --
        e.g. ``depth<2`` with low chars must not escalate."""
        assert _escalation_fires(ok=False, reason="depth<2", total_chars=10) is False

    def test_kill_switch_disabled_does_not_fire(self, monkeypatch):
        monkeypatch.setattr(client_mod, "_OCR_ESCALATION_GARBLE", False)
        assert _escalation_fires(ok=False, reason="node_count<3", total_chars=0) is False

    def test_env_override_of_char_floor(self, monkeypatch):
        monkeypatch.setattr(client_mod, "LOW_CONTENT_OCR_CHAR_FLOOR", 50)
        assert _escalation_fires(ok=False, reason="node_count<3", total_chars=38) is True
        assert _escalation_fires(ok=False, reason="node_count<3", total_chars=100) is False
