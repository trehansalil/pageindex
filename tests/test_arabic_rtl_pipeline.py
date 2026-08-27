"""Zone-3 Arabic/RTL Pipeline Blindness -- regression, contract, and wiring tests.

Validates the six Zone-3 remediation targets:
1. Script-aware flat-prefer guard (Arabic 1.5x multiplier vs 3.0x Latin)
2. Content-enriched ScriptContext (Latin filename + Arabic content -> Arab)
3. ensure_tessdata non-Latin verification when TESSDATA_PREFIX unset
4. table_is_rtl stability across multi-page merge chains
5. Arabic heading injection content-density revert guard
6. _recover_flat_prefer expected_script wiring
"""

from __future__ import annotations

import inspect
import os
import shutil
from unittest.mock import patch

import pytest

from pageindex_mcp.converters.headings import (
    _inject_arabic_structural_headings,
)
from pageindex_mcp.converters.ocr_langs import (
    TessdataUnavailableError,
    _LATIN_LANGS,
    _system_tessdata_cache,
    ensure_tessdata,
)
from pageindex_mcp.helpers.table_stitch import (
    _merge_continuation_table,
    stitch_continuation_tables,
)
from pageindex_mcp.helpers.tree_split import table_is_rtl
from pageindex_mcp.script import ScriptContext


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _make_table_block(headers: list[str], rows: list[list[str]]) -> dict:
    """Create a minimal table block dict."""
    return {
        "role": "table",
        "headers": headers,
        "rows": [headers, *rows],
    }


def _arabic_table_block() -> dict:
    """A table block with >30% Arabic chars so table_is_rtl returns True."""
    headers = ["البند", "2023", "2024"]  # البند
    rows = [
        ["الإيرادات", "100", "200"],  # الإيرادات
        ["المصروفات", "50", "80"],   # المصروفات
    ]
    return _make_table_block(headers, rows)


def _latin_table_block() -> dict:
    """A table block with <30% Arabic chars so table_is_rtl returns False."""
    headers = ["Item", "2023", "2024"]
    rows = [
        ["Revenue", "100", "200"],
        ["Expenses", "50", "80"],
    ]
    return _make_table_block(headers, rows)


def _continuation_block(headers: list[str], rows: list[list[str]]) -> dict:
    """A continuation table block (numeric-only headers)."""
    return _make_table_block(headers, rows)


# ═══════════════════════════════════════════════════════════════════════════
# Test 1 (regression): Script-aware flat-prefer guard
# ═══════════════════════════════════════════════════════════════════════════

class TestScriptAwareFlatPreferGuard:
    """Arabic docs use a 1.5x multiplier; Latin docs use 3.0x."""

    def test_arabic_multiplier_is_lower_than_latin(self):
        """The Arabic flat-prefer multiplier must be strictly lower
        than the default Latin/general multiplier to prevent
        heading-inflated trees from blocking flat fallback."""
        from pageindex_mcp.client.recovery import (
            _ARABIC_FLAT_PREFER_MULTIPLIER,
            _RFC029_FLAT_PREFER_MULTIPLIER,
        )
        assert _ARABIC_FLAT_PREFER_MULTIPLIER < _RFC029_FLAT_PREFER_MULTIPLIER, (
            f"Arabic multiplier ({_ARABIC_FLAT_PREFER_MULTIPLIER}) must be "
            f"< Latin multiplier ({_RFC029_FLAT_PREFER_MULTIPLIER})"
        )

    def test_arabic_multiplier_default_is_1_5(self):
        """Default Arabic flat-prefer multiplier must be 1.5."""
        from pageindex_mcp.client.recovery import _ARABIC_FLAT_PREFER_MULTIPLIER
        assert _ARABIC_FLAT_PREFER_MULTIPLIER == 1.5

    def test_latin_multiplier_default_is_3_0(self):
        """Default Latin flat-prefer multiplier must be 3.0."""
        from pageindex_mcp.client.recovery import _RFC029_FLAT_PREFER_MULTIPLIER
        assert _RFC029_FLAT_PREFER_MULTIPLIER == 3.0

    def test_flat_prefer_selects_arabic_multiplier_for_arab_script(self):
        """When expected_script='Arab', _recover_flat_prefer must use
        the lower Arabic multiplier, causing flat to win more easily
        (regression guard for marsoom-13 type documents)."""
        # Verify the method body references _ARABIC_FLAT_PREFER_MULTIPLIER
        # when expected_script == "Arab" by inspecting the source.
        from pageindex_mcp.client.recovery import RecoveryMixin
        src = inspect.getsource(RecoveryMixin._recover_flat_prefer)
        assert "_ARABIC_FLAT_PREFER_MULTIPLIER" in src, (
            "_recover_flat_prefer must reference _ARABIC_FLAT_PREFER_MULTIPLIER"
        )
        assert 'expected_script == "Arab"' in src, (
            "_recover_flat_prefer must branch on expected_script == 'Arab'"
        )


# ═══════════════════════════════════════════════════════════════════════════
# Test 2 (contract): Content-enriched ScriptContext
# ═══════════════════════════════════════════════════════════════════════════

class TestContentEnrichedScriptContext:
    """ScriptContext.from_document must detect Arabic from content text
    even when the filename suggests Latin or is script-neutral."""

    def test_english_filename_arabic_content_yields_arab(self):
        """An English-named PDF with Arabic body text must produce
        dominant_script='Arab' via content override."""
        arabic_text = "المادة " * 50  # المادة repeated
        ctx = ScriptContext.from_document("document.pdf", raw_text=arabic_text)
        assert ctx.dominant_script == "Arab", (
            f"Expected 'Arab', got '{ctx.dominant_script}' "
            f"(source={ctx.source})"
        )

    def test_english_filename_arabic_content_source_is_content_override(self):
        """When Arabic content overrides a Latin filename, source must
        reflect the override provenance."""
        arabic_text = "مرسوم بقانون " * 30
        ctx = ScriptContext.from_document("federal_decree.pdf", raw_text=arabic_text)
        assert ctx.source == "content_override", (
            f"Expected source='content_override', got '{ctx.source}'"
        )

    def test_arabic_filename_arabic_content_stays_arab(self):
        """Arabic filename + Arabic content: dominant_script stays 'Arab'."""
        arabic_text = "المادة " * 50
        ctx = ScriptContext.from_document(
            "مرسوم_13.pdf", raw_text=arabic_text
        )
        assert ctx.dominant_script == "Arab"

    def test_latin_filename_latin_content_stays_latin(self):
        """Latin filename + Latin content: must NOT change to Arab
        (regression guard for Latin documents)."""
        latin_text = "This is a standard English document about insurance terms. " * 50
        ctx = ScriptContext.from_document("terms_and_conditions.pdf", raw_text=latin_text)
        assert ctx.dominant_script != "Arab", (
            "Latin content must not trigger Arabic override"
        )

    def test_empty_content_preserves_filename_inference(self):
        """When raw_text is empty, ScriptContext relies on filename only."""
        ctx = ScriptContext.from_document("document.pdf", raw_text="")
        # 'document.pdf' has no Arabic script markers, so dominant_script
        # should be None or 'Latn' depending on filename inference.
        assert ctx.dominant_script != "Arab"
        assert ctx.source in ("filename", "none")


# ═══════════════════════════════════════════════════════════════════════════
# Test 3 (contract): ensure_tessdata non-Latin verification
# ═══════════════════════════════════════════════════════════════════════════

class TestEnsureTessdataNonLatinVerification:
    """ensure_tessdata must not silently assume non-Latin tessdata exists
    when TESSDATA_PREFIX is unset."""

    def test_latin_langs_pass_through_without_prefix(self, monkeypatch):
        """Latin languages should still pass through silently when
        TESSDATA_PREFIX is unset (no behavior change for Latin)."""
        monkeypatch.delenv("TESSDATA_PREFIX", raising=False)
        monkeypatch.delenv("TESSDATA_ALLOW_DOWNLOAD", raising=False)
        _system_tessdata_cache.clear()
        result = ensure_tessdata(["eng", "deu"])
        assert result == ["eng", "deu"]

    def test_non_latin_without_prefix_does_not_silently_pass(self, monkeypatch):
        """Non-Latin language (e.g. 'ara') with no TESSDATA_PREFIX must
        NOT silently return the language as available. It should either
        raise TessdataUnavailableError or verify via subprocess."""
        monkeypatch.delenv("TESSDATA_PREFIX", raising=False)
        monkeypatch.delenv("TESSDATA_ALLOW_DOWNLOAD", raising=False)
        _system_tessdata_cache.clear()

        # Mock shutil.which to return None (no tesseract binary found),
        # ensuring the system check fails and raises.
        monkeypatch.setattr(shutil, "which", lambda _name: None)

        with pytest.raises(TessdataUnavailableError, match="non-Latin tessdata missing"):
            ensure_tessdata(["ara"])

    def test_non_latin_with_system_tesseract_found_passes(self, monkeypatch):
        """When TESSDATA_PREFIX is unset but tesseract is found with the
        non-Latin traineddata file present, the language should pass."""
        monkeypatch.delenv("TESSDATA_PREFIX", raising=False)
        monkeypatch.delenv("TESSDATA_ALLOW_DOWNLOAD", raising=False)
        _system_tessdata_cache.clear()

        # Pre-populate cache to simulate successful system check
        _system_tessdata_cache["ara"] = True
        result = ensure_tessdata(["ara", "eng"])
        assert "ara" in result
        assert "eng" in result

    def test_non_latin_cached_failure_raises(self, monkeypatch):
        """When the system check cache says a non-Latin lang is missing,
        it should raise without re-probing."""
        monkeypatch.delenv("TESSDATA_PREFIX", raising=False)
        _system_tessdata_cache.clear()
        _system_tessdata_cache["ara"] = False

        with pytest.raises(TessdataUnavailableError):
            ensure_tessdata(["ara"])

    def test_ara_is_not_in_latin_langs(self):
        """Sanity: 'ara' must not be classified as a Latin language."""
        assert "ara" not in _LATIN_LANGS


class TestTessdataLatinSubstitutionClosure:
    """D5: requesting non-Latin langs that are all unavailable must raise,
    never silently fall back to Latin-only OCR."""

    def test_tessdata_raises_on_latin_only_substitution(self, monkeypatch, tmp_path):
        """Request ['ara', 'eng'], only 'eng' available → raises."""
        monkeypatch.setenv("TESSDATA_PREFIX", str(tmp_path))
        monkeypatch.delenv("TESSDATA_ALLOW_DOWNLOAD", raising=False)
        _system_tessdata_cache.clear()
        (tmp_path / "eng.traineddata").write_bytes(b"x")
        with pytest.raises(TessdataUnavailableError, match="non-Latin tessdata missing"):
            ensure_tessdata(["ara", "eng"])

    def test_tessdata_allows_pure_latin_request(self, monkeypatch, tmp_path):
        """Request ['deu', 'eng'], both available → no error."""
        monkeypatch.setenv("TESSDATA_PREFIX", str(tmp_path))
        monkeypatch.delenv("TESSDATA_ALLOW_DOWNLOAD", raising=False)
        _system_tessdata_cache.clear()
        (tmp_path / "deu.traineddata").write_bytes(b"x")
        (tmp_path / "eng.traineddata").write_bytes(b"x")
        result = ensure_tessdata(["deu", "eng"])
        assert result == ["deu", "eng"]


# ═══════════════════════════════════════════════════════════════════════════
# Test 4 (regression): table_is_rtl stability
# ═══════════════════════════════════════════════════════════════════════════

class TestTableIsRtlStability:
    """table_is_rtl must be computed once on the original anchor and
    threaded through the entire merge chain, not recomputed per-merge."""

    def test_table_is_rtl_arabic_block(self):
        """An Arabic-majority table block returns True."""
        block = _arabic_table_block()
        assert table_is_rtl(block) is True

    def test_table_is_rtl_latin_block(self):
        """A Latin-majority table block returns False."""
        block = _latin_table_block()
        assert table_is_rtl(block) is False

    def test_merge_continuation_table_accepts_is_rtl_param(self):
        """_merge_continuation_table must accept an is_rtl keyword arg
        that overrides internal table_is_rtl recomputation."""
        sig = inspect.signature(_merge_continuation_table)
        assert "is_rtl" in sig.parameters, (
            "_merge_continuation_table must have is_rtl parameter"
        )

    def test_stitch_continuation_tables_passes_anchor_is_rtl(self):
        """stitch_continuation_tables must compute table_is_rtl on the
        original anchor and pass it to _merge_continuation_table, so the
        RTL decision does not drift across merges."""
        src = inspect.getsource(stitch_continuation_tables)
        # The function should compute anchor_is_rtl before the merge loop
        assert "table_is_rtl" in src, (
            "stitch_continuation_tables must call table_is_rtl"
        )
        assert "is_rtl=" in src, (
            "stitch_continuation_tables must pass is_rtl= to "
            "_merge_continuation_table"
        )

    def test_merge_with_explicit_is_rtl_overrides_heuristic(self):
        """When is_rtl is explicitly passed, _merge_continuation_table
        must use it instead of calling table_is_rtl(anchor)."""
        anchor = _arabic_table_block()
        cont = _continuation_block(
            ["2025", "2026"],
            [["300", "400"], ["120", "150"]],
        )
        # Force is_rtl=False on an Arabic table -- should use LTR merge
        result_ltr = _merge_continuation_table(anchor, cont, is_rtl=False)
        # Force is_rtl=True -- should use RTL merge
        result_rtl = _merge_continuation_table(anchor, cont, is_rtl=True)
        # RTL and LTR merges produce different header orderings
        assert result_ltr["headers"] != result_rtl["headers"], (
            "is_rtl=True vs False must produce different header orderings"
        )

    def test_multi_page_merge_chain_stable_rtl_decision(self):
        """Across a 3-page merge chain, the RTL decision must remain
        stable (same as the original anchor's table_is_rtl)."""
        anchor = _arabic_table_block()
        original_is_rtl = table_is_rtl(anchor)
        assert original_is_rtl is True, "Test setup: anchor must be RTL"

        # Simulate 2 continuation pages
        cont1 = _continuation_block(["2025"], [["300"], ["120"]])
        cont2 = _continuation_block(["2026"], [["400"], ["150"]])

        blocks = [anchor, cont1, cont2]
        result = stitch_continuation_tables(blocks)
        # Should produce a single merged table (all continuations stitched)
        assert len(result) == 1, (
            f"Expected 1 merged table, got {len(result)} blocks"
        )


# ═══════════════════════════════════════════════════════════════════════════
# Test 5 (contract): Arabic heading injection revert guard
# ═══════════════════════════════════════════════════════════════════════════

class TestArabicHeadingInjectionRevertGuard:
    """Thin documents (<2000 content chars) with sparse Arabic markers
    should have heading injection reverted; substantial documents keep it."""

    def test_thin_doc_heading_injection_reverted(self):
        """A document with <2000 content chars and high heading ratio
        should have injection reverted (returns original markdown)."""
        # Build a thin Arabic doc: a few marker lines + minimal content
        lines = []
        for i in range(1, 6):
            lines.append(f"مادة ({i})")       # مادة (N) -- marker
            lines.append(f"نص قصير {i}")  # نص قصير N
        md = "\n".join(lines)
        # Content is very short (<2000 chars), heading ratio >30%
        assert len(md) < 2000, f"Test setup: md must be <2000 chars, got {len(md)}"
        result = _inject_arabic_structural_headings(md)
        # Reverted: result equals original (no headings injected)
        assert "# " not in result or result == md, (
            "Thin doc heading injection should be reverted"
        )

    def test_substantial_doc_heading_injection_kept(self):
        """A document with >5000 content chars keeps the injected headings."""
        lines = []
        for i in range(1, 11):
            lines.append(f"مادة ({i})")
            # Add substantial Arabic body text after each marker
            body = "المادة تنص على " * 60
            lines.append(body)
        md = "\n".join(lines)
        assert len(md) > 5000, f"Test setup: md must be >5000 chars, got {len(md)}"
        result = _inject_arabic_structural_headings(md)
        # At least some headings should be injected (## markers)
        heading_lines = [ln for ln in result.split("\n") if ln.startswith("## ")]
        assert len(heading_lines) > 0, (
            "Substantial doc should keep injected headings"
        )

    def test_non_arabic_doc_unaffected(self):
        """A Latin-only document should pass through without any heading
        injection (no Arabic markers to match)."""
        md = "This is a standard English document.\n" * 100
        result = _inject_arabic_structural_headings(md)
        assert result == md, "Non-Arabic doc must pass through unchanged"


# ═══════════════════════════════════════════════════════════════════════════
# Test 6 (wiring): _recover_flat_prefer accepts expected_script
# ═══════════════════════════════════════════════════════════════════════════

class TestRecoverFlatPreferWiring:
    """_recover_flat_prefer must accept expected_script parameter and
    be called with it from indexer.py."""

    def test_signature_includes_expected_script(self):
        """_recover_flat_prefer must have expected_script in its signature."""
        from pageindex_mcp.client.recovery import RecoveryMixin
        sig = inspect.signature(RecoveryMixin._recover_flat_prefer)
        params = list(sig.parameters.keys())
        assert "expected_script" in params, (
            f"_recover_flat_prefer signature must include expected_script. "
            f"Found params: {params}"
        )

    def test_expected_script_has_default_none(self):
        """expected_script should default to None for backward compat."""
        from pageindex_mcp.client.recovery import RecoveryMixin
        sig = inspect.signature(RecoveryMixin._recover_flat_prefer)
        param = sig.parameters["expected_script"]
        assert param.default is None, (
            f"expected_script default must be None, got {param.default}"
        )

    def test_call_site_passes_expected_script(self):
        """indexer.py must pass expected_script when calling
        _recover_flat_prefer (wiring check)."""
        from pageindex_mcp.client import indexer as _idx_mod
        src = inspect.getsource(_idx_mod)
        # The call site should include expected_script in the invocation
        assert "_recover_flat_prefer" in src, (
            "indexer.py must call _recover_flat_prefer"
        )
        # Find the line that calls _recover_flat_prefer and verify it
        # passes expected_script
        lines = src.split("\n")
        call_lines = [
            ln for ln in lines if "_recover_flat_prefer" in ln and "def " not in ln
        ]
        assert any("expected_script" in ln for ln in call_lines), (
            "_recover_flat_prefer call site in indexer.py must pass "
            "expected_script"
        )

    def test_arabic_flat_prefer_multiplier_imported_in_recovery(self):
        """_ARABIC_FLAT_PREFER_MULTIPLIER must be defined in recovery.py."""
        from pageindex_mcp.client import recovery as _rec_mod
        assert hasattr(_rec_mod, "_ARABIC_FLAT_PREFER_MULTIPLIER"), (
            "recovery.py must define _ARABIC_FLAT_PREFER_MULTIPLIER"
        )
        val = getattr(_rec_mod, "_ARABIC_FLAT_PREFER_MULTIPLIER")
        assert isinstance(val, float) and val > 0, (
            f"_ARABIC_FLAT_PREFER_MULTIPLIER must be a positive float, got {val}"
        )
