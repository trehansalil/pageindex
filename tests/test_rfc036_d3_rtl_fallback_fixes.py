"""RFC-036 D3: rtl_reversal joins the flat-routing whitelist instead of
raising LowQualityTreeError immediately when reconstruct_bidi_order does not
converge.

Property 8: a document where validate_tree returns reason='rtl_reversal' and
            the bidi repair does not converge SHALL route through flat
            extraction (via the flat-routing whitelist) instead of raising.
Property 9: if the flat text routed under Property 8 is also garbled per
            _flat_text_is_garbled, the reason SHALL be overridden to
            'garbling' and LowQualityTreeError SHALL be raised (Hard Rule 5,
            no bypass).
Integration: وارد رقم 597 (numeric-junk digit blob) still ends ERROR --
             both tree and flat paths are equally garbled -- confirming the
             garble gate remains the safety net, not a rejection weakened by
             the whitelist addition.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import pageindex_mcp.client as client_mod
from pageindex_mcp.client import CustomPageIndexClient
from pageindex_mcp.helpers import LowQualityTreeError, TreeDefect, TreeGateResult


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


def _wire_index(monkeypatch, *, validate_tree, flat_md: str):
    """Patch every collaborator index() touches on the PDF -> markdown route,
    forcing validate_tree='rtl_reversal' and the bidi repair to not converge
    (reconstruct_bidi_order is a no-op identity so the re-validate after
    repair still fails with 'rtl_reversal')."""
    monkeypatch.setattr(client_mod, "settings", _fake_settings())
    monkeypatch.setattr(client_mod, "hash_cache_get", lambda filename: None)
    monkeypatch.setattr(client_mod, "list_processed_docs", lambda: [])
    monkeypatch.setattr(client_mod, "hash_cache_set", MagicMock())
    monkeypatch.setattr(client_mod, "validate_tree", validate_tree)
    monkeypatch.setattr(client_mod, "reconstruct_bidi_order", lambda s: s)
    monkeypatch.setattr(client_mod, "split_oversized_leaf_nodes", lambda structure: structure)
    monkeypatch.setattr(client_mod, "_segment_table_nodes", lambda structure: structure)
    monkeypatch.setattr(
        client_mod,
        "pdf_markdown_converters",
        lambda: [("stub", lambda path: flat_md)],
    )
    mocks = {
        "save_doc": MagicMock(),
        "save_flat_doc": MagicMock(),
        "save_raw": MagicMock(),
        "save_doc_meta": MagicMock(),
        "FLAT_DOCS_TOTAL": MagicMock(),
        "LOW_QUALITY_TREES": MagicMock(),
        "OCR_ESCALATION_TOTAL": MagicMock(),
        "VLM_FALLBACK_TOTAL": MagicMock(),
        "RAW_UPLOAD_FAILURES": MagicMock(),
        "PDF_PRIMARY_CONVERTER_FAILURES": MagicMock(),
        "PDF_EXTRACT_FALLBACKS": MagicMock(),
        "find_prior_verdict": MagicMock(return_value=None),
    }
    for name, m in mocks.items():
        monkeypatch.setattr(client_mod, name, m)
    return mocks


@pytest.fixture
def pdf_file(tmp_path):
    path = tmp_path / "arabic.pdf"
    path.write_bytes(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n fake pdf bytes")
    return str(path)


def _rtl_tree():
    """A tree that fails validate_tree with 'rtl_reversal' on every call --
    simulating a repair that never converges."""
    return {
        "structure": [
            {"node_id": "n1", "title": "elpmaS", "text": "txet ybab", "nodes": []},
        ],
        "doc_description": "reversed doc",
    }


_CLEAN_ARABIC_FLAT_MD = "\n\n".join(
    f"مرحبا بكم في هذا المستند الرسمي رقم {i} الذي يحتوي على نص عربي صحيح وواضح "
    "يمتد على عدة أسطر ويصف محتوى الفقرة بشكل كامل ومفصل."
    for i in range(12)
)

_NUMERIC_JUNK_FLAT_MD = "651001429 6 1 mo/2025/597 5/8/2025 51001429 " * 40


class TestRtlReversalFlatFallback:
    """Property 8: rtl_reversal + non-converging repair routes to flat
    extraction instead of raising, when the flat text is clean."""

    async def test_clean_flat_text_persists_via_flat_routing_not_terminal_raise(
        self, monkeypatch, pdf_file
    ):
        # Arrange -- validate_tree always rejects as rtl_reversal (repair
        # never converges); the flat markdown is clean, well-formed Arabic.
        validate = MagicMock(return_value=TreeGateResult(ok=False, defect=TreeDefect.RTL_REVERSAL))
        mocks = _wire_index(monkeypatch, validate_tree=validate, flat_md=_CLEAN_ARABIC_FLAT_MD)
        c = CustomPageIndexClient(api_key="test-key")
        monkeypatch.setattr(c, "_run_md_to_tree", AsyncMock(return_value=_rtl_tree()))

        # Act
        doc_id = await c.index(pdf_file)

        # Assert -- routed through the flat success path (PASS/MARGINAL
        # artifact persisted), not rejected with LowQualityTreeError.
        assert isinstance(doc_id, str)
        mocks["save_flat_doc"].assert_called_once()
        mocks["save_doc"].assert_not_called()
        saved_call = mocks["save_flat_doc"].call_args
        verdict = saved_call.args[1]["verdict"]
        assert verdict in ("PASS", "MARGINAL")


class TestRtlReversalFlatGarbleGate:
    """Property 9: when the flat text routed under Property 8 is also
    garbled, the reason is overridden to 'garbling' and LowQualityTreeError
    is raised -- no bypass of Hard Rule 5."""

    async def test_garbled_flat_text_overrides_reason_and_raises(self, monkeypatch, pdf_file):
        # Arrange -- validate_tree always rejects as rtl_reversal; the flat
        # markdown routed to is also numeric junk (fails _flat_text_is_garbled).
        validate = MagicMock(return_value=TreeGateResult(ok=False, defect=TreeDefect.RTL_REVERSAL))
        mocks = _wire_index(monkeypatch, validate_tree=validate, flat_md=_NUMERIC_JUNK_FLAT_MD)
        c = CustomPageIndexClient(api_key="test-key")
        monkeypatch.setattr(c, "_run_md_to_tree", AsyncMock(return_value=_rtl_tree()))

        # Act / Assert -- garble gate fires, reason overridden to 'garbling',
        # terminal raise fires, zero output persisted.
        with pytest.raises(LowQualityTreeError) as exc_info:
            await c.index(pdf_file)

        assert "garbling" in str(exc_info.value)
        mocks["save_doc"].assert_not_called()
        mocks["save_flat_doc"].assert_not_called()
        mocks["LOW_QUALITY_TREES"].labels.assert_called_once_with(reason="garbling")


class TestWard597StillErrorsWithImprovedDiagnostics:
    """Integration: وارد رقم 597 -- both the tree path and the flat path are
    equally numeric-junk garbled, so the document still ends ERROR post-D3;
    the whitelist addition only helps documents where the flat path is
    clean. This regression-guards Hard Rule 5 and confirms the diagnostic
    log line fires on the flat-path garble gate."""

    async def test_ward_597_numeric_junk_still_raises_low_quality_tree_error(
        self, monkeypatch, pdf_file, caplog
    ):
        validate = MagicMock(return_value=TreeGateResult(ok=False, defect=TreeDefect.RTL_REVERSAL))
        mocks = _wire_index(monkeypatch, validate_tree=validate, flat_md=_NUMERIC_JUNK_FLAT_MD)
        c = CustomPageIndexClient(api_key="test-key")
        monkeypatch.setattr(c, "_run_md_to_tree", AsyncMock(return_value=_rtl_tree()))

        with caplog.at_level("WARNING"):
            with pytest.raises(LowQualityTreeError) as exc_info:
                await c.index(pdf_file)

        assert "garbling" in str(exc_info.value)
        mocks["save_doc"].assert_not_called()
        mocks["save_flat_doc"].assert_not_called()
        # Improved diagnostic logging: the flat-path garble gate trigger is
        # logged with the reason override, distinguishable from a bare
        # terminal rtl_reversal raise.
        assert any(
            "Flat-path garble gate triggered" in rec.message for rec in caplog.records
        )
