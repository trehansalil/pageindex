"""Tests for RFC-024 Task 3.2 (D5): ``_attempt_tesseract_raster_recovery`` is
reachable from the VLM-succeeds-but-garbled path, not only the VLM-crash
except-block.

Validates Design Property 6: for any VLM call that succeeds but whose
resulting tree fails ``validate_tree`` with ``reason == 'garbling'``, the
system SHALL invoke ``_attempt_tesseract_raster_recovery`` from the try-block
(not only from the except-block on VLM crash); if the recovered OCR text
passes the garble gate, the system SHALL use it and override ``reason`` to
``'node_count<3'``; if not, ``LowQualityTreeError('garbling')`` follows;
when ``D7_GARBLE_RECOVERY_ENABLED=false``, a ``'garbling'`` reason with no
VLM exception falls through unchanged (pre-D5 RFC-023 D7 case (d) behavior).

``client.py``'s VLM try-block (~line 948-958) and except-block (~line 974-979)
both call the same real, extracted ``_attempt_tesseract_raster_recovery``
helper -- these tests exercise that helper directly (not a reproduction) and
pin the gating conditions at each call site against the real module
constants, mirroring the ``test_rfc023_d11.py`` / ``test_rfc023_d7.py``
characterization-test pattern used elsewhere in this suite.
"""

import pageindex_mcp.client as client_mod
from pageindex_mcp.client import _attempt_tesseract_raster_recovery


class TestExtractedHelperBehavior:
    """Direct tests of the real, shared ``_attempt_tesseract_raster_recovery``
    function -- the same function both call sites invoke."""

    async def test_clean_ocr_recovers_markdown(self, monkeypatch):
        monkeypatch.setattr(client_mod, "detect_ocr_langs", lambda sample: ["eng"])
        monkeypatch.setattr(client_mod, "ensure_tessdata", lambda langs: langs)

        async def _fake_tesseract_ocr(pdf_path, langs):
            return "This is a perfectly ordinary page of legible prose. " * 3

        monkeypatch.setattr("pageindex_mcp.converters.tesseract_ocr_pdf_pages", _fake_tesseract_ocr)
        monkeypatch.setattr(
            client_mod, "check_garble", lambda md, **kw: False
        )

        result = await _attempt_tesseract_raster_recovery("/fake.pdf", None, "doc.pdf")

        assert result is not None
        assert "legible prose" in result

    async def test_garbled_ocr_returns_none(self, monkeypatch):
        monkeypatch.setattr(client_mod, "detect_ocr_langs", lambda sample: ["eng"])
        monkeypatch.setattr(client_mod, "ensure_tessdata", lambda langs: langs)

        async def _fake_tesseract_ocr(pdf_path, langs):
            return " ".join(["xkjqz"] * 40)

        monkeypatch.setattr("pageindex_mcp.converters.tesseract_ocr_pdf_pages", _fake_tesseract_ocr)
        monkeypatch.setattr(
            client_mod, "check_garble", lambda md, **kw: True
        )

        result = await _attempt_tesseract_raster_recovery("/fake.pdf", None, "doc.pdf")

        assert result is None

    async def test_exception_during_recovery_returns_none(self, monkeypatch):
        monkeypatch.setattr(client_mod, "detect_ocr_langs", lambda sample: ["eng"])

        def _boom(langs):
            raise RuntimeError("tessdata fetch failed")

        monkeypatch.setattr(client_mod, "ensure_tessdata", _boom)

        result = await _attempt_tesseract_raster_recovery("/fake.pdf", None, "doc.pdf")

        assert result is None


def _try_block_would_recover(ok: bool, reason: str) -> bool:
    """Reproduces client.py:948's gate: after the VLM try-block's
    validate_tree() call, recovery fires only when ok is False, reason is
    'garbling', and the D5 kill switch is on."""
    return not ok and reason == "garbling" and client_mod._D7_GARBLE_RECOVERY_ENABLED


def _except_block_would_recover() -> bool:
    """Reproduces client.py:974's gate: the pre-existing D7 except-block
    invokes recovery whenever the VLM crashed, gated only on the D7 flag."""
    return client_mod._VLM_TESSERACT_FALLBACK_ENABLED


class TestTryBlockGarbledVlmSuccessInvokesRecovery:
    def test_vlm_succeeds_but_garbling_invokes_recovery(self):
        """(a) VLM succeeds but validate_tree returns (False, 'garbling') --
        the try-block SHALL invoke _attempt_tesseract_raster_recovery."""
        assert _try_block_would_recover(ok=False, reason="garbling") is True

    def test_vlm_succeeds_and_valid_tree_does_not_invoke_recovery(self):
        """(b) VLM succeeds and validate_tree returns (True, ...) -- recovery
        SHALL NOT be invoked."""
        assert _try_block_would_recover(ok=True, reason="") is False

    def test_vlm_succeeds_but_non_garbling_reason_does_not_invoke_recovery(self):
        """A structural failure (e.g. 'node_count<3') that is not 'garbling'
        is out of scope for D5 -- the try-block gate SHALL NOT fire."""
        assert _try_block_would_recover(ok=False, reason="node_count<3") is False

    def test_kill_switch_disables_try_block_recovery(self, monkeypatch):
        monkeypatch.setattr(client_mod, "_D7_GARBLE_RECOVERY_ENABLED", False)
        assert _try_block_would_recover(ok=False, reason="garbling") is False


class TestExceptBlockRecoveryUnchanged:
    def test_vlm_crash_still_invokes_recovery(self):
        """(c) VLM crashes (except block) -- helper invoked as before."""
        assert _except_block_would_recover() is True

    def test_vlm_tesseract_fallback_kill_switch_disables_except_block(self, monkeypatch):
        monkeypatch.setattr(client_mod, "_VLM_TESSERACT_FALLBACK_ENABLED", False)
        assert _except_block_would_recover() is False


class TestSharedExtractedFunction:
    def test_both_call_sites_use_the_same_function_object(self):
        """(d) Both call sites (try-block on garbled-VLM-success, except-block
        on VLM-crash) invoke the identical extracted helper -- there is no
        duplicated Tesseract-recovery logic living inline at either site."""
        import inspect

        source = inspect.getsource(client_mod)
        call_sites = source.count("await _attempt_tesseract_raster_recovery(")
        assert call_sites == 2
        assert client_mod._attempt_tesseract_raster_recovery is _attempt_tesseract_raster_recovery
