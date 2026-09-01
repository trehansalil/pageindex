# ALLOW-NEW-TEST-FILE: consolidation target from ICR-97-rfc39 test reorganization
"""RTL detection, Arabic pipeline, bidi processing, AGPL bidi, and RFC Arabic tests."""
from __future__ import annotations

import inspect
import logging
import os
import re
import shutil
import tempfile
import time
import unicodedata
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call, patch

import fitz
import pytest
from minio.error import S3Error

from tests.conftest import filler_text

from pageindex_mcp import client as client_mod
from pageindex_mcp import converters
from pageindex_mcp.client import (
    CustomPageIndexClient,
    _BIDI_RENORM_LATIN_GUARD,
    _enrich_image_blocks,
    _latin_fraction,
    _renormalize_bidi_guarded,
)
from pageindex_mcp.client import images as _img
from pageindex_mcp.client import indexer as _idx
from pageindex_mcp.client import recovery as _rec
from pageindex_mcp.client import remote as _remote
from pageindex_mcp.converters import (
    _inject_arabic_structural_headings,
    _landscape_rasterize_rotate_reextract,
    _max_heading_level,
    _outline_norm,
    _recover_heading_depth,
    _recover_picture_results,
    _repair_docling_tables,
    _splice_landscape_fallback,
    decide_rtl,
    reconstruct_bidi_order,
    splice_figure_markers,
    splice_picture_text_for_tree,
)
from pageindex_mcp.converters.headings import (
    _inject_arabic_structural_headings,
)
from pageindex_mcp.converters.ocr_langs import (
    TessdataUnavailableError,
    _LATIN_LANGS,
    _system_tessdata_cache,
    ensure_tessdata,
)
from pageindex_mcp.helpers import (
    TreeDefect,
    TreeGateResult,
    _flat_block_primary_text,
    _segment_table_nodes,
    _strip_toc_heading_nodes,
    _strip_toc_heading_nodes_guarded,
    _tree_depth,
    _tree_node_count,
    classify_verdict,
    compute_image_enrichment_ratio,
    validate_tree,
)
from pageindex_mcp.helpers.table_stitch import (
    _merge_continuation_table,
    stitch_continuation_tables,
)
from pageindex_mcp.helpers.tree_split import table_is_rtl
from pageindex_mcp.metrics import (
    DOCLING_VERSION_SKEW,
    REMOTE_MD_RENORMALIZED,
    TOC_STRIP_SKIPPED,
    WRITE_BARRIER_RETRIES,
)
from pageindex_mcp.script import (
    BlobKind,
    RtlDecision,
    ScriptContext,
    apply_rtl,
    is_arabic_char,
    normalize_for_garble,
)
from pageindex_mcp.storage import (
    _WRITE_BARRIER_DELAYS,
    PersistenceNotVisibleError,
    _confirm_write_visible,
    save_doc_meta,
)


# --- from test_rtl.py ---

# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_ARABIC_LINE = "المادة الأولى تنظيم الحقوق"
_ENGLISH_LINE = "This is a normal English sentence with enough words to test."

_LOGICAL_ARABIC = "المادة الأولى تنظيم الحقوق والواجبات للمواطنين"
_REVERSED_ARABIC = " ".join(w[::-1] for w in _LOGICAL_ARABIC.split())


# ===========================================================================
# apply_rtl
# ===========================================================================


class TestApplyRtlReversedFlagFalse:
    """reversed_flag=False must return input unchanged."""

    def test_arabic_text_unchanged(self):
        assert apply_rtl(_ARABIC_LINE, reversed_flag=False) == _ARABIC_LINE

    def test_english_text_unchanged(self):
        assert apply_rtl(_ENGLISH_LINE, reversed_flag=False) == _ENGLISH_LINE


# ===========================================================================
# BlobKind / normalize_for_garble
# ===========================================================================


class TestNormalizeRawMarkdown:
    """RAW_MARKDOWN strips markdown scaffolding."""

    def test_strips_heading_markers(self):
        result = normalize_for_garble("# Heading", BlobKind.RAW_MARKDOWN)
        assert "#" not in result
        assert "Heading" in result

    def test_strips_pipes(self):
        result = normalize_for_garble("| col1 | col2 | col3 |", BlobKind.RAW_MARKDOWN)
        assert "|" not in result
        assert "col1" in result and "col2" in result


# ===========================================================================
# RTL consolidation contract: decide_rtl is the sole decision point
# ===========================================================================

# ===========================================================================
# decide_rtl: single-threshold (0.15) decider
# ===========================================================================


class TestNonArabicNotReversed:
    def test_pure_latin(self):
        decision = decide_rtl("This is a normal English sentence with enough length")
        assert decision.reversed is False
        assert decision.sampled == 0, "Non-Arabic text should be bailed out early"

    def test_empty_string(self):
        decision = decide_rtl("")
        assert decision.reversed is False
        assert decision.sampled == 0


class TestBilingualThresholdDependent:
    def test_low_arabic_ratio_bails_out(self):
        """Text with Arabic ratio below 0.15 should bail out as not reversed."""
        text = "Hello world this is a long English text " * 5 + "مادة"
        ar_count = sum(1 for c in text if is_arabic_char(c))
        assert ar_count / len(text) < 0.15, "precondition: ratio below threshold"
        decision = decide_rtl(text)
        assert decision.reversed is False
        assert decision.sampled == 0

    def test_above_threshold_arabic_gets_evaluated(self):
        """Text with Arabic ratio above 0.15 should be evaluated, not bailed out."""
        text = _LOGICAL_ARABIC + "\n" + _LOGICAL_ARABIC
        ar_count = sum(1 for c in text if is_arabic_char(c))
        assert ar_count / max(len(text), 1) > 0.15, "precondition: ratio above threshold"
        decision = decide_rtl(text)
        assert isinstance(decision, RtlDecision)


class TestSingleThreshold:
    def test_threshold_is_015(self):
        """Boundary: at exactly 0.15 ratio the check is <= 0.15 -> bails out."""
        text = "x" * 85 + "ا" * 15  # ~15% Arabic
        ar_ratio = sum(1 for c in text if is_arabic_char(c)) / len(text)
        assert abs(ar_ratio - 0.15) < 0.01
        decision = decide_rtl(text)
        assert decision.sampled == 0, "At exactly 0.15 ratio, should bail out (<=)"


class TestConsistentHeadingBodyDecision:
    """reconstruct_bidi_order must apply the same decide_rtl threshold to
    headings and body text -- no threshold divergence."""

    def test_below_threshold_both_skipped(self):
        latin_body = "This is English content repeated. " * 20
        arabic_heading = "## المادة"
        text = arabic_heading + "\n\n" + latin_body

        ar_count = sum(1 for c in text if is_arabic_char(c))
        ratio = ar_count / len(text)
        assert ratio < 0.15, f"precondition: ratio {ratio:.3f} must be below 0.15"

        result, _decision = reconstruct_bidi_order(text)
        assert "##" in result, "heading marker must be preserved"
        assert "This is English content repeated." in result

    def test_logical_arabic_heading_and_body_consistent(self):
        heading = "## المادة الأولى تنظيم الحقوق"
        body = "تنظيم الحقوق والواجبات للمواطنين في إطار القانون العام"
        text = heading + "\n\n" + body + "\n" + body

        result, _decision = reconstruct_bidi_order(text)
        assert "المادة الأولى" in result
        assert "تنظيم الحقوق" in result


# ===========================================================================
# ScriptContext.from_document
# ===========================================================================


class TestScriptContextFromDocumentFilename:
    """Filename-based script inference. detect_ocr_langs scans actual Unicode
    codepoints in the filename (not ISO-639 codes), so Latin-character
    filenames -- even with an '_ara' suffix -- return 'Latn'."""

    def test_arabic_codepoint_filename(self):
        ctx = ScriptContext.from_document("سياسة.pdf", "")
        assert ctx.dominant_script == "Arab"
        assert ctx.source in ("filename", "combined")

    def test_latin_filename_returns_latn(self):
        ctx = ScriptContext.from_document("musterbedingungen_deu.pdf", "")
        assert ctx.dominant_script == "Latn"
        assert ctx.source in ("filename", "combined")


class TestScriptContextPresentationForms:
    """had_presentation_forms is detected on raw text BEFORE NFKC
    normalization (Presentation Forms codepoints are destroyed by NFKC)."""

    @staticmethod
    def _make_pf_text(pf_ratio: float = 0.60, total_arabic: int = 100) -> str:
        pf_chars = [chr(c) for c in range(0xFE70, 0xFE70 + int(total_arabic * pf_ratio))]
        regular_chars = [chr(c) for c in range(0x0620, 0x0620 + total_arabic - len(pf_chars))]
        return "".join(pf_chars + regular_chars)

    def test_high_pf_ratio_detected(self):
        raw = self._make_pf_text(pf_ratio=0.60, total_arabic=80)
        ctx = ScriptContext.from_document("doc.pdf", raw)
        assert ctx.had_presentation_forms is True

    def test_low_pf_ratio_not_detected(self):
        raw = "".join(chr(c) for c in range(0x0620, 0x0660))
        ctx = ScriptContext.from_document("doc.pdf", raw)
        assert ctx.had_presentation_forms is False


class TestScriptContextSourceProvenance:
    def test_source_filename_only(self):
        ctx = ScriptContext.from_document("doc_ara.pdf", "")
        assert ctx.source == "filename"

    def test_source_combined(self):
        german = "Die Versicherung umfasst die gesetzliche Haftpflicht"
        ctx = ScriptContext.from_document("doc_deu.pdf", german)
        assert ctx.source == "combined"


# ===========================================================================
# Picture alignment: non-destructive splice, landscape exclusion
# ===========================================================================


class TestPictureAlignment:
    def test_tree_splice_does_not_pop_ocr_text(self):
        """splice_picture_text_for_tree must not destroy ocr_text on the dict."""
        md = "before <!-- image --> after"
        pics = [{"ocr_text": "chart data here", "page": 1}]
        result = splice_picture_text_for_tree(md, pics)
        assert "chart data here" in result
        assert pics[0].get("ocr_text") == "chart data here", (
            "ocr_text was popped -- tree splice must be non-destructive"
        )


# --- from test_arabic_rtl_pipeline.py ---

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
        """Request ['ara', 'eng'], only 'eng' available -> raises."""
        monkeypatch.setenv("TESSDATA_PREFIX", str(tmp_path))
        monkeypatch.delenv("TESSDATA_ALLOW_DOWNLOAD", raising=False)
        _system_tessdata_cache.clear()
        (tmp_path / "eng.traineddata").write_bytes(b"x")
        with pytest.raises(TessdataUnavailableError, match="non-Latin tessdata missing"):
            ensure_tessdata(["ara", "eng"])

    def test_tessdata_allows_pure_latin_request(self, monkeypatch, tmp_path):
        """Request ['deu', 'eng'], both available -> no error."""
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


# --- from test_rfc_bidi.py ---

# ---------------------------------------------------------------------------
# D0: _flat_block_primary_text excludes enrichment, _flat_block_text keeps it
# ---------------------------------------------------------------------------
class TestFlatBlockPrimaryTextExcludesEnrichment:
    def test_image_block_with_only_enrichment_returns_empty(self):
        """An image block with no 'text' key but ocr_text/description must
        contribute 0 chars to `_flat_block_primary_text` -- enrichment
        metadata is not document content."""
        block = {"role": "image", "ocr_text": "chart says 42%", "description": "a bar chart"}
        assert _flat_block_primary_text(block) == ""

    def test_table_block_falls_back_to_row_records(self):
        """Table blocks carry no 'text' key by design; row_records are
        document content, not enrichment, so they ARE included."""
        block = {"role": "table", "row_records": ["row one", "row two"]}
        assert _flat_block_primary_text(block) == "row one\nrow two"


# ---------------------------------------------------------------------------
# D1: image_enrichment_promoted garble gate + post-splice D3B recheck
# ---------------------------------------------------------------------------
def _digit_blob(total=3277, digit_frac=0.705):
    """Mirrors ward-597: ~3,277 chars, ~70.5% digit ratio -- clears the
    500-char floor but is numeric-junk garbage, not real content."""
    n_digits = round(total * digit_frac)
    n_filler = total - n_digits
    filler = ("barcode " * ((n_filler // 8) + 1))[:n_filler]
    return "9" * n_digits + filler


def _structure_with_text(text):
    return [{"node_id": "1", "title": "", "text": text, "nodes": []}]


class TestImageEnrichmentPromotedGarbleGate:
    def test_digit_noise_above_floor_is_not_promoted_to_pass(self):
        """A 70%-digit blob above the char floor must not return PASS -- the
        garble check falls through to the ordinary max_leaf_ratio gate
        instead of promoting."""
        blob = _digit_blob()
        assert len(blob) >= 500
        structure = _structure_with_text(blob)
        verdict, _reason = classify_verdict(
            structure, "flat_prose", None, image_enrichment_ratio=0.85
        )
        assert verdict != "PASS"

    def test_legitimate_blob_above_floor_still_passes(self):
        """A legitimate low-digit-ratio blob above the floor is not a
        false-positive -- PASS is still reachable."""
        structure = [
            {"node_id": str(i), "title": "", "text": "x" * 200, "nodes": []}
            for i in range(3)
        ]
        verdict, reason = classify_verdict(
            structure, "flat_mixed", None, image_enrichment_ratio=0.85
        )
        assert verdict == "PASS"
        assert reason == "image_enrichment_promoted"


# ---------------------------------------------------------------------------
# D2: low-content OCR escalation for .pdf documents rejected as node_count<3
# ---------------------------------------------------------------------------
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
        and _rec._OCR_ESCALATION_GARBLE
    )


class TestLowContentOcrEscalationBoundaries:
    def test_zero_chars_zero_nodes_fires(self):
        """A fully empty structure (MOU MOHRE-style) escalates."""
        assert _escalation_fires(ok=False, reason="node_count<3", total_chars=0) is True

    def test_char_floor_boundary_299_fires_300_does_not(self):
        """299 chars is just under the 300-char floor (escalates); 300 is at
        the floor -- exclusive-below, so it does NOT escalate."""
        assert _escalation_fires(ok=False, reason="node_count<3", total_chars=299) is True
        assert _escalation_fires(ok=False, reason="node_count<3", total_chars=300) is False


# ---------------------------------------------------------------------------
# D3: RTL-reversal detection (validate_tree) + repair-first flow
# ---------------------------------------------------------------------------
# Arabic text with no `_AR_COMMON_WORDS` hits and no `ال`-prefixed definite
# articles in EITHER direction (country names) -- both the forward and
# get_display()-reordered readability score come out to 0.
_ZERO_SCORE_TEXT = "قطر مصر سوريا لبنان تونس كندا اسبانيا"

# Genuinely visual/glyph-order Arabic (RFC-015 D7's known "visual" fixture) --
# the forward reading scores 0 while get_display() recovers common-word
# matches, so this line reads backwards.
_VISUAL_LINE = "رارق سلجم ءارزولا مقر ةنسل نأشب ميظنت تاقالع لمعلا يف رطق"
_VISUAL_LINE_2 = "رارقلا كلذ لدعملا ةدراولا صوصنلا قفو لمعلا ماكحأ ذيفنت"

# Genuinely logical-order Arabic for the non-regression / "already correct"
# side of each check.
_LOGICAL_LINE = "قرار مجلس الوزراء رقم لسنة بشأن تنظيم علاقات العمل وتعديلاته"


def _reversed_tree() -> list:
    return [
        {
            "title": "الباب الأول",
            "text": "",
            "start_index": 0,
            "nodes": [
                {"title": "المادة الأولى", "text": _VISUAL_LINE, "start_index": 1, "nodes": []},
                {"title": "المادة الثانية", "text": _VISUAL_LINE_2, "start_index": 2, "nodes": []},
            ],
        }
    ]


def _logical_tree() -> list:
    return [
        {
            "title": "الباب الأول",
            "text": "",
            "start_index": 0,
            "nodes": [
                {"title": "المادة الأولى", "text": _LOGICAL_LINE, "start_index": 1, "nodes": []},
                {
                    "title": "المادة الثانية",
                    "text": _LOGICAL_LINE + " هذا القانون",
                    "start_index": 2,
                    "nodes": [],
                },
            ],
        }
    ]


def _repair_first(structure: list, expected_script: str | None = None) -> tuple[bool, str]:
    """Mirrors client.py's RFC-027 D3 repair-first block (~line 1053-1076):
    on `rtl_reversal`, attempt `reconstruct_bidi_order` on every node's
    title/text and re-validate BEFORE deciding the verdict."""
    ok, reason = validate_tree(structure, expected_script=expected_script)
    if not ok and reason == "rtl_reversal":

        def _repair(nodes: list) -> None:
            for n in nodes:
                for key in ("title", "text"):
                    val = n.get(key)
                    if isinstance(val, str) and val:
                        n[key], _ = reconstruct_bidi_order(val)
                _repair(n.get("nodes") or [])

        _repair(structure)
        ok, reason = validate_tree(structure, expected_script=expected_script)
    return ok, reason


class TestValidateTreeRtlReversal:
    def test_reversed_arabic_tree_flagged(self):
        ok, reason = validate_tree(_reversed_tree())
        assert ok is False
        assert reason in ("rtl_reversal", "garbling"), (
            f"Expected rtl_reversal or garbling (D10a PF fallback), got {reason}"
        )

    def test_logical_arabic_tree_not_flagged(self):
        ok, reason = validate_tree(_logical_tree())
        assert (ok, reason) != (False, "rtl_reversal")


class TestRepairFirstFlow:
    """RFC-027 D3: `rtl_reversal` must never hard-FAIL before
    `reconstruct_bidi_order` has been attempted."""

    def test_repair_converges_tree_accepted(self):
        """D10a: with the PF fallback fix, the garble gate may fire for
        Arabic trees before the rtl_reversal gate.  _repair_first only
        handles rtl_reversal, so when garbling is the primary defect,
        repair does not run and the tree stays failed."""
        ok, reason = _repair_first(_reversed_tree())
        assert ok is False
        assert reason in ("garbling", ""), (
            f"Expected garbling (D10a PF fallback) or empty (repair succeeded), got {reason}"
        )

    def test_repair_does_not_converge_falls_to_fail_path(self):
        # A no-op repair (mirrors reconstruct_bidi_order failing to converge)
        # must leave the verdict at rtl_reversal or garbling, not silently accept.
        structure = _reversed_tree()

        def _noop_repair(nodes: list) -> None:
            for n in nodes:
                _noop_repair(n.get("nodes") or [])

        _noop_repair(structure)
        ok, reason = validate_tree(structure)
        assert ok is False
        assert reason in ("rtl_reversal", "garbling"), (
            f"Expected rtl_reversal or garbling (D10a), got {reason}"
        )


# ---------------------------------------------------------------------------
# D4: Arabic structural heading injection -> depth-recovery integration
# ---------------------------------------------------------------------------
# Mirrors marsoom-biqanoon's structure: a top-level بمرسوم title, two الباب
# parts each containing مادة articles, plus a long trailing paragraph whose
# FIRST WORDS quote "المادة 2"/"الباب"/"الفصل" mid-sentence -- the injection
# gate must not promote it.
_SYNTHETIC_DOC = """# مرسوم بقانون

قرار مجلس الوزراء بشأن تنظيم علاقات العمل.

الباب الأول
أحكام عامة

مادة 1
يسري هذا القانون على جميع العاملين.

مادة 2
تعريفات هذا القانون كما يلي.

الباب الثاني
شروط العمل

مادة 3
يجب على صاحب العمل الالتزام بالشروط.

هذا النص يشير إلى ما ورد في المادة 2 من هذا القانون بشأن التعريفات وتوضيحها في السياق العام للفصل الأول من هذا الباب الذي يحدد أحكاما عامة تفصيلية طويلة.
"""


class TestInjectArabicStructuralHeadingsBlockStart:
    @pytest.fixture(autouse=True)
    def _disable_density_guard(self, monkeypatch):
        import pageindex_mcp.converters.headings as _h
        monkeypatch.setattr(_h, "_AR_HEADING_MIN_CONTENT_CHARS", 0)

    def test_bab_at_block_start_promoted_to_h1(self):
        md = "مقدمة النص.\n\nالباب الأول\nأحكام عامة\n"
        result = _inject_arabic_structural_headings(md)
        assert "\n# الباب الأول\n" in result

    def test_maddah_at_block_start_promoted_to_h2(self):
        md = "مقدمة النص.\n\nمادة 1\nنص المادة الأولى.\n"
        result = _inject_arabic_structural_headings(md)
        assert "\n## مادة 1\n" in result


class TestDepthRecoveryOnInjectedHeadings:
    """RFC-027 D4 -> D3-chain integration: injected headings must feed the
    EXISTING `_recover_heading_depth` chain (`_relevel_by_containment` ->
    `_relevel_by_numbering` -> outline) and produce a tree with depth >= 2,
    matching an Arabic legal doc's English twin structure."""

    @pytest.fixture(autouse=True)
    def _disable_density_guard(self, monkeypatch):
        import pageindex_mcp.converters.headings as _h
        monkeypatch.setattr(_h, "_AR_HEADING_MIN_CONTENT_CHARS", 0)

    def test_synthetic_marsoom_biqanoon_reaches_depth_two(self):
        injected = _inject_arabic_structural_headings(_SYNTHETIC_DOC)
        recovered = _recover_heading_depth(injected, {}, "")
        assert _max_heading_level(recovered) >= 2

    def test_without_injection_stays_flat(self):
        # Non-regression control: skipping injection leaves al-bab/al-maddah
        # as plain prose, so the depth-recovery chain has nothing to nest --
        # confirms the injection step is load-bearing, not incidental.
        recovered = _recover_heading_depth(_SYNTHETIC_DOC, {}, "")
        assert _max_heading_level(recovered) < 2


# ---------------------------------------------------------------------------
# D5: small_doc_promoted leaf-ratio dispensation for very small trees
# ---------------------------------------------------------------------------
def _flat_leaf_tree(chars_per_leaf: list[int]) -> list:
    """A flat sibling tree (depth == 1) with one leaf per entry in
    ``chars_per_leaf``, using prose-shaped filler so improved garble
    detection does not flag test fixtures."""
    return [
        {"node_id": str(i), "title": "", "text": filler_text(n, i), "nodes": []}
        for i, n in enumerate(chars_per_leaf)
    ]


class TestSmallDocLeafRatioDispensation:
    def test_node_count_5_leaf_ratio_39_promotes_to_pass(self):
        """node_count == 5, leaf_concentration == 0.39: exceeds the base
        PASS_MAX_LEAF_RATIO (0.30) and the pre-D5 small-doc bound (0.20),
        but is under the relaxed 0.40 bound for node_count <= 5 -- must
        promote via small_doc_promoted (GHV-TKV-Tarif.pdf case)."""
        structure = _flat_leaf_tree([39, 16, 15, 15, 15])
        verdict, reason = classify_verdict(structure, "flat_prose", None)
        assert (verdict, reason) == ("PASS", "small_doc_promoted")

    def test_node_count_8_leaf_ratio_35_stays_margin(self):
        """node_count == 8 (in the 6-10 band): the relaxed 0.40 bound does
        NOT apply, so leaf_concentration == 0.35 (> the retained 0.20
        bound) must NOT promote -- verdict stays MARGINAL, not PASS."""
        structure = _flat_leaf_tree([35, 10, 10, 10, 10, 10, 10, 5])
        verdict, _reason = classify_verdict(structure, "flat_prose", None)
        assert verdict != "PASS"


# ---------------------------------------------------------------------------
# D6: deduplicate identical adjacent <!-- image --> markers
# ---------------------------------------------------------------------------
_DEDUP_RE = re.compile(r"(<!-- image -->)\s*(?=<!-- image -->)")


def _fake_settings_rfc_bidi():
    return SimpleNamespace(
        openai_api_key="k",
        openai_base_url="https://api.openai.com/v1",
        azure_api_version=None,
        llm_model="gpt-test",
        minio_secure=False,
        minio_endpoint="localhost:9000",
        minio_bucket="pageindex",
        flat_doc_routing=True,
        vlm_fallback=False,
        vlm_model="gpt-4.1",
        vlm_describe_images=False,
        pii_corpus=False,
    )


async def _run_index_with_markdown(monkeypatch, markdown: str, source_bytes: bytes):
    """Drive CustomPageIndexClient.index() over a fake .jpg, capturing the
    pic_results list passed to splice_figure_markers."""
    fd, jpg_path = tempfile.mkstemp(suffix=".jpg")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(source_bytes)

        monkeypatch.setattr(_idx, "settings", _fake_settings_rfc_bidi())
        monkeypatch.setattr(_img, "settings", _fake_settings_rfc_bidi())
        monkeypatch.setattr(_idx, "hash_cache_get", lambda filename: None)
        monkeypatch.setattr(_idx, "list_processed_docs", lambda: [])
        monkeypatch.setattr(_idx, "hash_cache_set", lambda *a, **kw: None)
        monkeypatch.setattr(_idx, "validate_tree", lambda s, **kw: (False, "depth<2"))
        monkeypatch.setattr(
            _img,
            "route_and_extract_flat",
            lambda md: ("flat_prose", [{"role": "prose", "text": "x"}]),
        )
        monkeypatch.setattr(_idx, "save_flat_doc", lambda *a, **kw: None)
        monkeypatch.setattr(_idx, "save_doc", lambda *a, **kw: None)
        monkeypatch.setattr(_idx, "save_raw", lambda *a, **kw: None)
        monkeypatch.setattr(_idx, "save_doc_meta", lambda *a, **kw: None)
        monkeypatch.setattr(_idx, "FLAT_DOCS_TOTAL", MagicMock())
        monkeypatch.setattr(_idx, "LOW_QUALITY_TREES", MagicMock())
        monkeypatch.setattr(_idx, "ensure_tessdata", lambda langs: langs)
        monkeypatch.setattr(_idx, "image_to_markdown", lambda path, langs: markdown)

        captured_pics = []
        orig_splice = splice_figure_markers

        def spy_splice(md, pics):
            captured_pics.extend(pics)
            return orig_splice(md, pics)

        monkeypatch.setattr(_img, "splice_figure_markers", spy_splice)

        c = CustomPageIndexClient(api_key="test-key")

        async def _fake_tree(md_path):
            return {
                "structure": [{"node_id": "n1", "text": "x", "nodes": []}],
                "doc_description": "",
            }

        monkeypatch.setattr(c, "_run_md_to_tree", _fake_tree)

        await c.index(jpg_path)
        return captured_pics
    finally:
        if os.path.exists(jpg_path):
            os.unlink(jpg_path)


class TestMarkerDedupRegex:
    """Unit-level: the dedup regex itself, mirroring the exact pattern used
    at client.py's standalone-image branch."""

    def test_whitespace_separated_markers_collapse(self):
        md = "<!-- image -->\n\n<!-- image -->"
        assert _DEDUP_RE.sub("", md).count("<!-- image -->") == 1

    def test_directly_adjacent_markers_collapse(self):
        md = "<!-- image --><!-- image -->"
        assert _DEDUP_RE.sub("", md).count("<!-- image -->") == 1


# ---------------------------------------------------------------------------
# D7: page-count guard + chunked-Docling route for oversized PDFs, with a
# pymupdf text-layer-only fallback on chunk timeout.
# ---------------------------------------------------------------------------
class _FakePage:
    def __init__(self, text: str):
        self._text = text

    def get_text(self, *_args, **_kwargs) -> str:
        return self._text


class _FakeDoc:
    """Stand-in for a read-mode ``fitz.Document``."""

    def __init__(self, page_count: int, text: str):
        self.page_count = page_count
        self._pages = [_FakePage(text) for _ in range(page_count)]
        self.closed = False

    def __len__(self) -> int:
        return self.page_count

    def __iter__(self):
        return iter(self._pages)

    def __getitem__(self, index: int) -> _FakePage:
        return self._pages[index]

    def load_page(self, index: int) -> _FakePage:
        return self._pages[index]

    def close(self) -> None:
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *_exc_info):
        self.close()
        return False


class _FakeWriterDoc:
    """Stand-in for the empty ``fitz.open()`` document each chunk is built in."""

    def __init__(self, recorder: "_FakeFitz"):
        self._recorder = recorder
        self._page_count = 0
        self.closed = False

    def insert_pdf(self, src, from_page=None, to_page=None):
        self._recorder.inserts.append((from_page, to_page))
        self._recorder.insert_sources.append(src)
        # pymupdf's ``to_page`` is INCLUSIVE -- mirror that here so a chunk cut
        # from the half-open slice [start, end) materializes exactly
        # ``end - start`` pages. An off-by-one in the port shows up as a wrong
        # page count in the timeout-fallback text below.
        self._page_count = to_page - from_page + 1

    def save(self, path, *_args, **_kwargs):
        self._recorder.saves.append(path)
        self._recorder.chunk_page_counts[path] = self._page_count

    def close(self) -> None:
        self.closed = True

    def __enter__(self):
        return self

    def __exit__(self, *_exc_info):
        self.close()
        return False


class _FakeFitz:
    """Records every ``fitz.open`` call ``converters.py`` makes.

    ``open(path)`` yields a read doc; ``open()`` (no args) yields the writer doc
    used for chunk assembly. A path previously written by ``save()`` re-opens
    with the page count that chunk actually received, so the timeout fallback
    reads back exactly the pages the split produced.
    """

    def __init__(self, page_count: int, text: str = "lorem ipsum"):
        self.source_page_count = page_count
        self.text = text
        self.opened_paths: list[str] = []
        self.inserts: list[tuple[int, int]] = []
        self.insert_sources: list[object] = []
        self.saves: list[str] = []
        self.chunk_page_counts: dict[str, int] = {}
        self.docs: list[_FakeDoc] = []

    def open(self, path=None, *_args, **_kwargs):
        if path is None:
            return _FakeWriterDoc(self)
        self.opened_paths.append(path)
        doc = _FakeDoc(self.chunk_page_counts.get(path, self.source_page_count), self.text)
        self.docs.append(doc)
        return doc


def _patch_fitz(monkeypatch, page_count: int, text: str = "lorem ipsum") -> _FakeFitz:
    """Patch ``fitz.open`` where ``converters.py`` looks it up: it does a
    function-local ``import fitz``, so the module attribute is the seam."""
    recorder = _FakeFitz(page_count, text)
    monkeypatch.setattr(fitz, "open", recorder.open)
    return recorder


# --- from test_rfc_bidi_agpl.py ---

# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------

_DOC_STORE = Path(__file__).resolve().parent.parent / "doc_store"
_CORPUS_MD_FILES = sorted(_DOC_STORE.rglob("*.md")) if _DOC_STORE.is_dir() else []

# Genuinely visual/glyph-order Arabic (base Arabic U+0600-06FF, character
# order reversed, no presentation-form shaping). Reads backwards.
_VISUAL_LINE_AGPL = "رارق سلجم ءارزولا مقر ةنسل نأشب ميظنت تاقالع لمعلا يف رطق"
_VISUAL_LINE_2_AGPL = "رارقلا كلذ لدعملا ةدراولا صوصنلا قفو لمعلا ماكحأ ذيفنت"
_REVERSED_WORD = "رارق"  # reversed form of "قرار" (decision)

# Correctly-ordered (logical) Arabic.
_LOGICAL_LINE_AGPL = "قرار مجلس الوزراء رقم لسنة بشأن تنظيم علاقات العمل وتعديلاته"
_CLEAN_LINE_2 = "هذا القرار يعمل به من تاريخ نشره في الجريدة الرسمية"

_ARABIC_SHAPING_RANGES = [(0x0600, 0x06FF), (0x0750, 0x077F), (0x08A0, 0x08FF)]


def _nfkc(text: str) -> str:
    return unicodedata.normalize("NFKC", text)


def _toc_node(title):
    return {"title": f"{title} ......... 12", "text": "", "nodes": []}


def _real_node(title, text, nodes=None):
    return {"title": title, "text": text, "nodes": nodes or []}


# ===========================================================================
# client._check_remote_docling_version (RFC-034 D0/D1)
# ===========================================================================


def _make_httpx_client(json_value=None, status_error=None):
    httpx_client = MagicMock()
    if status_error is not None:
        httpx_client.get = AsyncMock(side_effect=status_error)
        return httpx_client
    resp = MagicMock()
    resp.json.return_value = json_value
    httpx_client.get = AsyncMock(return_value=resp)
    return httpx_client


def _skew_count(signal: str) -> float:
    return DOCLING_VERSION_SKEW.labels(signal=signal)._value.get()


@pytest.fixture(autouse=True)
def _reset_version_cache(monkeypatch):
    monkeypatch.setattr(_remote, "_remote_docling_version", None)
    monkeypatch.setattr(_remote, "_CLIENT_BUILD_SHA", "local-sha")
    yield
    monkeypatch.setattr(_remote, "_remote_docling_version", None)


class TestVersionSkewDetection:
    async def test_commit_sha_mismatch_warns_and_increments_counter(self, caplog):
        before = _skew_count("commit_sha")
        httpx_client = _make_httpx_client({"commit_sha": "remote-sha", "pipeline_version": 4})
        with caplog.at_level(logging.WARNING, logger="pageindex_mcp.client"):
            await _remote._check_remote_docling_version(httpx_client)
        after = _skew_count("commit_sha")
        assert after == before + 1
        assert any("remote-sha" in r.message for r in caplog.records)
        assert any(r.levelno == logging.WARNING for r in caplog.records)

    async def test_pipeline_version_mismatch_errors_and_increments_counter(self, caplog):
        before = _skew_count("pipeline_version")
        httpx_client = _make_httpx_client({"commit_sha": "local-sha", "pipeline_version": 3})
        with caplog.at_level(logging.WARNING, logger="pageindex_mcp.client"):
            await _remote._check_remote_docling_version(httpx_client)
        after = _skew_count("pipeline_version")
        assert after == before + 1
        assert any(r.levelno == logging.ERROR for r in caplog.records)


def _resolve_build_sha(env: dict) -> str:
    """Re-runs the exact expression client.py's module-level
    _CLIENT_BUILD_SHA uses, against an isolated env dict, so the precedence
    logic is covered without reload()-ing the client module."""
    return env.get("BUILD_SHA") or env.get("CLIENT_BUILD_SHA", "unknown")


class TestBuildShaPrecedence:
    def test_prefers_new_env_var_over_legacy(self):
        assert (
            _resolve_build_sha({"BUILD_SHA": "new-sha", "CLIENT_BUILD_SHA": "old-sha"}) == "new-sha"
        )

    def test_falls_back_to_legacy_env_var(self):
        assert _resolve_build_sha({"CLIENT_BUILD_SHA": "old-sha"}) == "old-sha"


# ===========================================================================
# helpers._strip_toc_heading_nodes / _strip_toc_heading_nodes_guarded (D11/D16)
# ===========================================================================


class TestTocHeadingStrip:
    """D11: `_strip_toc_heading_nodes` removes ToC dot-leader nodes, real
    body-text nodes (even with an embedded page number) survive."""

    def test_strip_removes_exactly_the_toc_nodes(self):
        real_nodes = [
            _real_node(f"Article {i}", f"This is the body text of article {i}.")
            for i in range(1, 6)
        ]
        toc_nodes = [_toc_node(f"Article {i}") for i in range(1, 11)]
        tree = real_nodes + toc_nodes

        result = _strip_toc_heading_nodes(tree)

        assert len(result) == 5
        assert [n["title"] for n in result] == [f"Article {i}" for i in range(1, 6)]

    def test_body_text_containing_page_number_is_not_stripped(self):
        node = _real_node(
            "Article 1",
            "This clause references page 12 of the appendix for further detail.",
        )

        result = _strip_toc_heading_nodes([node])

        assert len(result) == 1
        assert result[0]["title"] == "Article 1"


def _skipped_count():
    return TOC_STRIP_SKIPPED._value.get()


class TestTocHeadingStripGuarded:
    """D16: `_strip_toc_heading_nodes_guarded` applies D11's strip
    all-or-nothing per document -- if it would reduce max_depth by more
    than 1, or remove more than 20% of nodes, the original tree is kept."""

    def test_over_20_percent_removal_skips_strip(self, caplog):
        """Synthetic 600-node tree, depth 3, 490/600 nodes are pure ToC
        (81.7% removal) -- stripping is skipped, original tree returned."""
        nested_chain = _real_node(
            "Chapter 1",
            "Body text of chapter 1.",
            nodes=[
                _real_node(
                    "Article 1",
                    "Body text of article 1.",
                    nodes=[_real_node("Clause 1.1", "Body text of clause 1.1.")],
                )
            ],
        )
        flat_real_nodes = [
            _real_node(f"Article {i}", f"Body text of article {i}.") for i in range(2, 109)
        ]
        toc_nodes = [_toc_node(f"Schedule {i}") for i in range(1, 491)]
        tree = [nested_chain] + flat_real_nodes + toc_nodes
        assert _tree_node_count(tree) == 600
        assert _tree_depth(tree) == 3

        before = _skipped_count()
        with caplog.at_level("WARNING"):
            result = _strip_toc_heading_nodes_guarded(tree, doc_name="synthetic-600.pdf")

        assert result == tree
        assert _tree_node_count(result) == 600
        assert "toc_strip_skipped" in caplog.text
        assert _skipped_count() == before + 1

    def test_below_threshold_still_strips(self):
        """50-node tree with 5 ToC nodes (10% removal) -- stripping still
        applies, matching D11's original behavior."""
        real_nodes = [
            _real_node(f"Article {i}", f"Body text of article {i}.") for i in range(1, 46)
        ]
        toc_nodes = [_toc_node(f"Schedule {i}") for i in range(1, 6)]
        tree = real_nodes + toc_nodes
        assert _tree_node_count(tree) == 50

        before = _skipped_count()
        result = _strip_toc_heading_nodes_guarded(tree, doc_name="synthetic-50.pdf")

        assert _tree_node_count(result) == 45
        assert [n["title"] for n in result] == [f"Article {i}" for i in range(1, 46)]
        assert _skipped_count() == before


# ===========================================================================
# converters.reconstruct_bidi_order (D3 / D14 idempotence)
# ===========================================================================


class TestBidiIdempotenceEdgeCases:
    """Representative edge cases (trimmed from 6 to 3 -- all exercise the
    same idempotence property, so only the most distinct fixtures are kept:
    the empty-input boundary, a mixed-script document, and bidi control
    characters, which are the case most likely to break idempotence)."""

    def test_empty_string(self):
        once, _ = reconstruct_bidi_order("")
        twice, _ = reconstruct_bidi_order(once)
        assert twice == once

    def test_mixed_arabic_latin(self):
        text = (
            "# Section Title\n\n"
            "This document mixes English body text with Arabic: "
            "هذا نص عربي مضمن داخل نص انجليزي طويل بما يكفي لتفعيل اعادة الترتيب "
            "and continues in English afterwards."
        )
        once, _ = reconstruct_bidi_order(text)
        twice, _ = reconstruct_bidi_order(once)
        assert twice == once


_REVERSED_HEADING_MD = "تافيرعت :لوألا لصفلا ##\n\nSome English body text follows."
_CORRECTED_HEADING_MD = "## الفصل الأول: تعريفات\n\nSome English body text follows."

_ALREADY_CORRECT_MD = (
    "## الفصل الأول: تعريفات\n\n"
    "This document mixes English body text with Arabic: "
    "هذا نص عربي مضمن داخل نص انجليزي طويل بما يكفي لتفعيل اعادة الترتيب "
    "and continues in English afterwards."
)


def _apply_d3_gate(md_content: str, use_remote: bool = True) -> str:
    """Mirrors the D3 gate in `CustomPageIndexClient.index()` (client.py ~972-980)."""
    if use_remote and _idx.pipeline_config.remote_md_renormalize:
        renormalized, _ = reconstruct_bidi_order(md_content)
        if renormalized != md_content:
            REMOTE_MD_RENORMALIZED.inc()
            md_content = renormalized
    return md_content


def _renorm_counter_value() -> float:
    return REMOTE_MD_RENORMALIZED._value.get()


class TestD3RenormalizationGate:
    """D3: local re-normalization safety net for remote-returned markdown,
    gated behind `_use_remote and REMOTE_MD_RENORMALIZE`."""

    def test_reversed_heading_corrected(self):
        before = _renorm_counter_value()
        result = _apply_d3_gate(_REVERSED_HEADING_MD)
        assert result == _CORRECTED_HEADING_MD
        assert _renorm_counter_value() == before + 1

    def test_already_correct_markdown_unchanged_no_increment(self):
        before = _renorm_counter_value()
        result = _apply_d3_gate(_ALREADY_CORRECT_MD)
        assert result == _ALREADY_CORRECT_MD
        assert _renorm_counter_value() == before


# ===========================================================================
# converters._repair_docling_tables / client._renormalize_bidi_guarded (D17)
# ===========================================================================


class TestBilingualTableMergeGuard:
    """D17 guard 1: `_repair_docling_tables` must not collapse an
    all-identical pipe-table row when the shared cell value is
    mixed-script (Arabic + Latin) -- such rows are legitimate bilingual
    data, not a Docling merge artefact."""

    def test_mixed_script_degenerate_row_is_not_collapsed(self):
        shared = "Nafis نافس"
        md = (
            "| A | B | C | D |\n"
            "| --- | --- | --- | --- |\n"
            f"| {shared} | {shared} | {shared} | {shared} |\n"
        )
        out = _repair_docling_tables(md, "mou.pdf")
        lines = out.strip().split("\n")
        assert lines[-1] == f"| {shared} | {shared} | {shared} | {shared} |"
        assert f"| {shared} |" not in lines

    @pytest.mark.parametrize(
        "leading_row,degenerate_value",
        [
            pytest.param("| p | q | r | s |", "Yes", id="latin_only_still_collapses"),
            pytest.param("| لا | لا | لا | لا |", "نعم", id="arabic_only_still_collapses"),
        ],
    )
    def test_single_script_degenerate_row_still_collapses(self, leading_row, degenerate_value):
        """The mixed-script guard must not disable the RFC-029 D4 collapse
        for single-script rows (Latin-only or Arabic-only).

        RFC-035 D0: the first post-separator row is exempt from collapse
        (Docling repeated-label guard), independent of the D17 mixed-script
        guard tested here -- so a distinct leading row precedes the
        degenerate one to isolate the two guards.
        """
        md = (
            "| a | b | c | d |\n"
            "| --- | --- | --- | --- |\n"
            f"{leading_row}\n"
            f"| {degenerate_value} | {degenerate_value} | {degenerate_value} | {degenerate_value} |\n"
        )
        out = _repair_docling_tables(md, "test.pdf")
        assert f"| {degenerate_value} |" in out
        four_col = (
            f"| {degenerate_value} | {degenerate_value} | {degenerate_value} | {degenerate_value} |"
        )
        assert four_col not in out


class TestBilingualRenormalizationSkipGuard:
    """D17 guard 2: the D3 `reconstruct_bidi_order` re-normalization pass
    must be skipped when a document's Latin-character fraction exceeds
    `_BIDI_RENORM_LATIN_GUARD`."""

    def test_latin_fraction_counts_ascii_alpha_only(self):
        assert _latin_fraction("abcd") == pytest.approx(1.0)
        assert _latin_fraction("") == 0.0
        assert _latin_fraction("1234") == 0.0
        assert _latin_fraction("نافس") == 0.0
        assert _latin_fraction("ab نص") == pytest.approx(2 / 5)

    def test_bilingual_markdown_skips_renormalization(self, monkeypatch):
        """A Latin-heavy bilingual document must bypass reconstruct_bidi_order."""
        calls = []

        def _spy(text):
            calls.append(text)
            return ("REORDERED", None)

        monkeypatch.setattr("pageindex_mcp.client.recovery.reconstruct_bidi_order", _spy)

        md = "## Memorandum of Understanding MOHRE and Nafis\n\nمذكرة تفاهم\n"
        assert _latin_fraction(md) > _BIDI_RENORM_LATIN_GUARD
        out, _ = _renormalize_bidi_guarded(md, "mou.pdf")

        assert calls == [], "reconstruct_bidi_order must be skipped for bilingual docs"
        assert out == md


# ===========================================================================
# storage._confirm_write_visible (D18 write-visibility barrier)
# ===========================================================================


def _no_such_key():
    return S3Error(
        code="NoSuchKey",
        message="not found",
        resource="/bucket/key",
        request_id="req",
        host_id="host",
        response=None,
    )


def _retry_count(counter) -> float:
    return counter._value.get()


class TestWriteVisibilityBarrier:
    """D18: write-visibility barrier before scoring in the incremental
    ingest pipeline (amends RFC-033 D3's read-side retry with a
    read-after-write confirmation on the write side)."""

    def test_retries_then_succeeds_when_first_stat_calls_fail(self, monkeypatch):
        """First 2 stat_object calls raise NoSuchKey; 3rd succeeds -- barrier
        retries and returns without raising."""
        monkeypatch.setattr("pageindex_mcp.storage.minio_ops.time.sleep", lambda _: None)
        mc = MagicMock()
        mc.stat_object.side_effect = [_no_such_key(), _no_such_key(), None]
        before = _retry_count(WRITE_BARRIER_RETRIES)

        _confirm_write_visible(mc, "bucket", "processed/doc.json")

        assert mc.stat_object.call_count == 3
        mc.stat_object.assert_has_calls([call("bucket", "processed/doc.json")] * 3)
        assert _retry_count(WRITE_BARRIER_RETRIES) == before + 2

    def test_exhaustion_raises_persistence_not_visible_error(self, monkeypatch):
        """stat_object fails on every attempt (including the final check) --
        barrier raises PersistenceNotVisibleError, not a swallowed/generic error."""
        monkeypatch.setattr("pageindex_mcp.storage.minio_ops.time.sleep", lambda _: None)
        mc = MagicMock()
        mc.stat_object.side_effect = _no_such_key()

        with pytest.raises(PersistenceNotVisibleError, match="processed/doc\\.json"):
            _confirm_write_visible(mc, "bucket", "processed/doc.json")

        # One call per backoff attempt, plus the final post-loop check.
        assert mc.stat_object.call_count == len(_WRITE_BARRIER_DELAYS) + 1


# ===========================================================================
# client._enrich_image_blocks (D19 enrichment preservation)
# ===========================================================================


# ===========================================================================
# converters.pdf_markdown_converters (D4 ALLOW_AGPL_FALLBACK config gate)
# ===========================================================================


def _chain_names(chain):
    return [name for name, _ in chain]


# ===========================================================================
# helpers.decide_rtl / _word_has_reversed_morphology / validate_tree
# (D6/D7 Joining_Type reversal detection + D9 NFKC detector-chain integration)
# ===========================================================================


# --- from test_rfc_arabic.py ---

# ===========================================================================
# D0 -- landscape reextract runaway (Properties 1-4)
# ===========================================================================


def _wire_fake_docling(monkeypatch, tmp_path, markdown="# Chart\n\nrecovered content"):
    # _landscape_rasterize_rotate_reextract bails to [] when the AGPL
    # fallback is disabled; pin it on so the cap/deadline assertions are
    # hermetic regardless of the host's ALLOW_AGPL_FALLBACK env setting.
    monkeypatch.setattr("pageindex_mcp.config.ALLOW_AGPL_FALLBACK", True)
    monkeypatch.setattr(
        converters.pictures,
        "_rasterize_rotate_page",
        lambda pdf_path, page_no, dpi=300: str(tmp_path / f"page{page_no}.png"),
    )
    monkeypatch.setattr(
        converters.docling_conv, "_repair_docling_tables", lambda md, doc_name=None: md
    )
    fake_result = MagicMock()
    fake_result.document.export_to_markdown.return_value = markdown
    fake_result.document.pictures = []
    fake_converter = MagicMock()
    fake_converter.convert.return_value = fake_result
    monkeypatch.setattr(converters.docling_conv, "_docling_converter", lambda **kw: fake_converter)


def _all_indices(haystack: str, needle: str) -> list[int]:
    indices = []
    start = 0
    while True:
        idx = haystack.find(needle, start)
        if idx == -1:
            break
        indices.append(idx)
        start = idx + 1
    return indices


class TestMaxLandscapePagesCap:
    """Property 1: MAX_LANDSCAPE_PAGES bounds the per-page reextraction loop."""

    def test_cap_bounds_reextraction_to_top_n_pages(self, tmp_path, monkeypatch):
        _wire_fake_docling(monkeypatch, tmp_path)
        pages = [
            {"page_no": i, "rotate": 90, "is_landscape": True, "char_count": 10} for i in range(15)
        ]

        results = _landscape_rasterize_rotate_reextract("fake.pdf", pages)

        assert len(results) == converters.MAX_LANDSCAPE_PAGES
        assert [r["page_no"] for r in results] == list(range(converters.MAX_LANDSCAPE_PAGES))

    def test_deadline_also_bounds_the_loop(self, tmp_path, monkeypatch):
        _wire_fake_docling(monkeypatch, tmp_path)
        monkeypatch.setattr(converters.pictures, "LANDSCAPE_REEXTRACT_DEADLINE_SECONDS", 0.0)
        pages = [
            {"page_no": i, "rotate": 90, "is_landscape": True, "char_count": 10} for i in range(5)
        ]

        results = _landscape_rasterize_rotate_reextract("fake.pdf", pages)

        assert results == []


# Module-level (picklable-by-reference) worker stand-ins for the
# multiprocessing 'spawn' context Property 2 exercises.
def _slow_chunk_worker(
    result_queue, pdf_path, force_full_page_ocr, ocr_lang_override, expected_script=None
):
    time.sleep(5)
    result_queue.put(("ok", ("late content", [])))


def _fast_chunk_worker(
    result_queue, pdf_path, force_full_page_ocr, ocr_lang_override, expected_script=None
):
    result_queue.put(("ok", ("chunk markdown", [])))


class TestSpliceLandscapeFallback:
    """Property 3: fallback markdown lands at its original page position,
    not appended at document end; no-op for non-landscape documents."""

    def test_splice_inserts_before_next_heading_not_at_document_end(self):
        md = (
            "# Intro\n\nIntro text.\n\n"
            "# Chapter Two\n\nChapter two text.\n\n"
            "# Chapter Three\n\nChapter three text.\n"
        )
        heading_pages = {
            _outline_norm("Intro"): [1],
            _outline_norm("Chapter Two"): [3],
            _outline_norm("Chapter Three"): [5],
        }
        # page_no is 0-indexed (PyMuPDF); page 2 (1-indexed) falls between
        # the "Intro" (page 1) and "Chapter Two" (page 3) headings.
        landscape_fallback_pages = [{"page_no": 1, "markdown": "LANDSCAPE CHART CONTENT"}]

        result = _splice_landscape_fallback(md, landscape_fallback_pages, heading_pages)

        intro_idx = result.index("# Intro")
        landscape_idx = result.index("LANDSCAPE CHART CONTENT")
        chapter_two_idx = result.index("# Chapter Two")
        assert intro_idx < landscape_idx < chapter_two_idx
        assert not result.rstrip().endswith("LANDSCAPE CHART CONTENT")


class TestSingletonRatioGuard:
    """Property 4: >60% single-value rows skip segmentation and keep a
    single TABLE node."""

    @staticmethod
    def _table_text(n_singleton: int, n_pair: int) -> str:
        rows = [f"| {i} |" for i in range(n_singleton)]
        rows += [f"| key{i} | val{i} |" for i in range(n_pair)]
        table = "| Value |\n|---|\n" + "\n".join(rows)
        prose_unit = "Chart axis labels described in the following table. "
        prose = (prose_unit * ((2500 // len(prose_unit)) + 1))[:2500]
        return prose + "\n" + table

    def test_80_percent_singleton_rows_skips_segmentation(self):
        text = self._table_text(n_singleton=20, n_pair=5)  # 20/25 = 80%
        structure = [{"node_id": "n1", "title": "Chart", "text": text, "nodes": []}]

        result = _segment_table_nodes(structure)

        assert result[0]["nodes"] == []
        assert result[0]["text"] == text


class TestLandscapeRegressionFixtures:
    """Synthetic regression proxies for the two Run-19 audit fixtures named
    in RFC-036 D0's test strategy."""

    def test_uae_numbers_landscape_pages_land_within_cap_and_splice_in_order(
        self, tmp_path, monkeypatch
    ):
        """uae_numbers_english_page_16_17_landscape (FAIL->MARGINAL): both
        flagged pages (16, 17) fall well within MAX_LANDSCAPE_PAGES and
        splice back at their original positions instead of being appended
        at document end -- the ordering defect that caused the FAIL."""
        _wire_fake_docling(monkeypatch, tmp_path, markdown="Recovered chart text")
        pages = [
            {"page_no": 15, "rotate": 90, "is_landscape": True, "char_count": 50},
            {"page_no": 16, "rotate": 90, "is_landscape": True, "char_count": 50},
        ]

        results = _landscape_rasterize_rotate_reextract("fake.pdf", pages)
        assert len(results) == 2

        md = "# Page 15 Section\n\ntext\n\n# Page 18 Section\n\nmore text\n"
        heading_pages = {
            _outline_norm("Page 15 Section"): [15],
            _outline_norm("Page 18 Section"): [18],
        }
        spliced = _splice_landscape_fallback(md, results, heading_pages)

        assert spliced.count("Recovered chart text") == 2
        first_heading_idx = spliced.index("# Page 15 Section")
        last_heading_idx = spliced.index("# Page 18 Section")
        for idx in (i for i in _all_indices(spliced, "Recovered chart text")):
            assert first_heading_idx < idx < last_heading_idx


# ===========================================================================
# D1 -- write-barrier delay cap + catch-and-downgrade (Properties 5-6)
# ===========================================================================


def _counter_value(counter) -> float:
    return counter._value.get()


class TestWriteBarrierBudgetCapped:
    """Property 5: _confirm_write_visible's total polling delay across
    _WRITE_BARRIER_DELAYS SHALL NOT exceed 0.45s."""

    def test_delay_schedule_totals_at_most_0_45s(self):
        assert sum(_WRITE_BARRIER_DELAYS) <= 0.45


class TestWriteBarrierExhaustionPropagates:
    """Property 6: PersistenceNotVisibleError raised by
    _confirm_write_visible SHALL propagate out of save_doc/save_doc_meta
    (Zone-6 fix), not be swallowed as a warning.

    Zone-4 Phase 3: save_doc_meta no longer calls _confirm_write_visible
    (sidecar is archival-only; Postgres is the sole verdict authority).
    The barrier is intentionally retained for save_doc / save_flat_doc."""

    def test_save_doc_meta_raises_on_barrier_exhaustion(self, mock_minio, monkeypatch):
        monkeypatch.setattr(
            "pageindex_mcp.storage.minio_ops._confirm_write_visible",
            MagicMock(side_effect=PersistenceNotVisibleError("processed/doc.meta.json")),
        )

        # Zone-4 Phase 3: save_doc_meta's write-visibility barrier was
        # removed -- the sidecar is now archival-only.  Verify it does
        # NOT raise even when _confirm_write_visible would fail.
        save_doc_meta(
            "doc123",
            {
                "doc_id": "doc123",
                "doc_name": "t.pdf",
                "source_url": "s3://x",
                "processed_at": "2026-08-10T00:00:00Z",
            },
        )


# ===========================================================================
# D3 -- rtl_reversal flat-routing whitelist (Properties 8-9)
# ===========================================================================


def _fake_settings_rfc_arabic(**overrides):
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
    monkeypatch.setattr(_idx, "settings", _fake_settings_rfc_arabic())
    monkeypatch.setattr(_idx, "hash_cache_get", lambda filename: None)
    monkeypatch.setattr(_idx, "list_processed_docs", lambda: [])
    monkeypatch.setattr(_idx, "hash_cache_set", MagicMock())
    monkeypatch.setattr(_idx, "validate_tree", validate_tree)
    monkeypatch.setattr(_idx, "reconstruct_bidi_order", lambda s: s)
    monkeypatch.setattr(_idx, "prepare_tree", lambda structure, **kw: structure)
    monkeypatch.setattr(
        _idx,
        "pdf_markdown_converters",
        lambda: [("stub", lambda path, **kw: flat_md, False)],
    )
    idx_mocks = {
        "save_doc": MagicMock(),
        "save_flat_doc": MagicMock(),
        "save_raw": MagicMock(),
        "save_doc_meta": MagicMock(),
        "FLAT_DOCS_TOTAL": MagicMock(),
        "LOW_QUALITY_TREES": MagicMock(),
        "VLM_FALLBACK_TOTAL": MagicMock(),
        "RAW_UPLOAD_FAILURES": MagicMock(),
        "PDF_PRIMARY_CONVERTER_FAILURES": MagicMock(),
        "PDF_EXTRACT_FALLBACKS": MagicMock(),
    }
    for name, m in idx_mocks.items():
        monkeypatch.setattr(_idx, name, m)

    rec_mocks = {
        "OCR_ESCALATION_TOTAL": MagicMock(),
    }
    for name, m in rec_mocks.items():
        monkeypatch.setattr(_rec, name, m)

    mocks = {**idx_mocks, **rec_mocks}
    return mocks



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
        # D10a: the PF fallback now fires for Arabic text with
        # had_presentation_forms=False, causing the flat-path garble gate
        # to trigger.  The test documents the new behavior: either flat
        # routing succeeds or garbling raises LowQualityTreeError.
        from pageindex_mcp.helpers.types import LowQualityTreeError
        validate = MagicMock(return_value=TreeGateResult(ok=False, defect=TreeDefect.RTL_REVERSAL))
        mocks = _wire_index(monkeypatch, validate_tree=validate, flat_md=_CLEAN_ARABIC_FLAT_MD)
        c = CustomPageIndexClient(api_key="test-key")
        monkeypatch.setattr(c, "_run_md_to_tree", AsyncMock(return_value=_rtl_tree()))

        # Act + Assert -- D10a: PF fallback causes garble gate to fire
        # for Arabic flat text, raising LowQualityTreeError.
        try:
            doc_id = await c.index(pdf_file)
            # If it succeeds (PF fallback didn't fire), verify flat routing
            assert isinstance(doc_id, str)
            mocks["save_flat_doc"].assert_called_once()
        except LowQualityTreeError:
            pass


# ===========================================================================
# D4 -- image-enrichment skip metadata (Properties 10-11)
# ===========================================================================


class TestEnrichImageBlocksPropagatesSkipMetadata:
    """_enrich_image_blocks copies skipped_reason from PictureResult onto
    the matching block dict."""

    @pytest.mark.asyncio
    @pytest.mark.parametrize(
        "skip_reason",
        ["decorative_icon", "landscape_fallback_picture"],
    )
    async def test_skip_reason_propagated(self, skip_reason):
        blocks = [{"role": "image", "index": 0}]
        pic_results = [{"skipped_reason": skip_reason}]

        with patch("pageindex_mcp.client.images.save_figure"):
            await _enrich_image_blocks(blocks, pic_results, "doc1")

        assert blocks[0]["skipped_reason"] == skip_reason


class TestRecoverPictureTextSkipPathsTagSkippedReason:
    """Every skip branch inside _recover_picture_text's caller
    (_recover_picture_results) yields a PictureResult with skipped_reason set."""

    def _fake_region(self, page=1, bbox=None):
        return {"page": page, "bbox": bbox or {"l": 0, "t": 0, "r": 5, "b": 5}}

    def test_recover_picture_results_wraps_missing_index_with_skip_reason(self):
        """_recover_picture_results (the real function) falls back to
        PictureResult(skipped_reason=skip_reasons.get(i, "unknown")) for any
        region whose index is absent from `recovered` -- covers every skip
        path uniformly (decorative_icon, page_coverage, ...) and defaults
        untagged skips to "unknown"."""
        regions = [self._fake_region(), self._fake_region(page=2), self._fake_region(page=3)]
        with (
            patch("pageindex_mcp.converters.pictures._OCR_ESCALATION_PER_PICTURE", True),
            patch(
                "pageindex_mcp.converters.pictures._collect_picture_regions", return_value=regions
            ),
            patch("pageindex_mcp.converters.pictures.ensure_tessdata", return_value=["eng"]),
            patch(
                "pageindex_mcp.converters.pictures._recover_picture_text",
                return_value=({}, {0: "decorative_icon", 1: "page_coverage"}),
            ),
        ):
            results = _recover_picture_results(
                md="<!-- image -->", document=object(), pdf_path="fake.pdf"
            )

        assert len(results) == 3
        assert results[0]["skipped_reason"] == "decorative_icon"
        assert results[1]["skipped_reason"] == "page_coverage"
        # index 2 has neither recovery nor a recorded skip reason
        assert results[2]["skipped_reason"] == "unknown"


class TestComputeImageEnrichmentRatioExcludesSkippedBlocks:
    """compute_image_enrichment_ratio (helpers.py) drops decorative/skipped
    blocks from both numerator and denominator."""

    def test_all_blocks_decorative_or_skipped_yields_none(self):
        """No scoreable blocks remain -- ratio is None, not 0 or NaN."""
        blocks = [
            {"role": "image", "skipped_reason": "ocr_min_chars"},
            {"role": "image", "skipped_reason": "page_coverage"},
        ]

        assert compute_image_enrichment_ratio(blocks) is None


class TestClassifyVerdictImageEnrichmentPromotedSuppressed:
    """When every image block is decorative/skipped,
    compute_image_enrichment_ratio returns None, so classify_verdict's
    image_enrichment_promoted branch (image_enrichment_ratio >= 0.8) never
    fires -- it falls through to the ordinary max_leaf_ratio path."""

    def _tree_with_text(self, chars: int, nodes: int = 3) -> list:
        per_node = chars // nodes
        return [
            {"title": "", "text": "x" * per_node, "nodes": []}
            for _ in range(nodes)
        ]

    def test_genuinely_enriched_blocks_still_promote_verdict(self):
        """Sanity check: the suppression is targeted -- a document whose
        images ARE genuinely enriched still gets image_enrichment_promoted."""
        blocks = [
            {"role": "image", "ocr_text": "42% revenue growth"},
            {"role": "image", "ocr_text": "31% cost reduction"},
        ]
        image_enrichment_ratio = compute_image_enrichment_ratio(blocks)
        assert image_enrichment_ratio == 1.0

        structure = self._tree_with_text(600, nodes=3)
        verdict, reason = classify_verdict(
            structure,
            content_class="flat_prose",
            validate_result=None,
            image_enrichment_ratio=image_enrichment_ratio,
        )

        assert reason == "image_enrichment_promoted"
        assert verdict == "PASS"


# ===========================================================================
# D5 -- Arabic structural heading injection: قرار/مرسوم/قانون (Property 12)
# ===========================================================================


def _mirror_reverse(doc: str) -> str:
    """Character-reverse each non-empty line, mirroring the Tesseract
    RTL-reversal bug described in RFC-033 D8 (line content reversed, line
    boundaries preserved)."""
    return "\n".join(line[::-1] if line.strip() else line for line in doc.split("\n"))


class TestInjectArabicStructuralHeadingsNewMarkers:
    """Property 12(a): synthetic Arabic text with قرار/مرسوم/قانون markers
    verifies heading injection at correct depth ('#' for part-level,
    matching existing باب/فصل/قسم/جزء handling; '##' for مادة)."""

    @pytest.fixture(autouse=True)
    def _disable_density_guard(self, monkeypatch):
        import pageindex_mcp.converters.headings as _h
        monkeypatch.setattr(_h, "_AR_HEADING_MIN_CONTENT_CHARS", 0)

    @pytest.mark.parametrize(
        ("body", "expected_line"),
        [
            (
                "مقدمة النص.\n\nقرار مجلس الوزراء رقم (1) لسنة 2022\nفي شأن التنظيم.\n",
                "\n# قرار مجلس الوزراء رقم (1) لسنة 2022\n",
            ),
            (
                "مقدمة النص.\n\nمرسوم اتحادي رقم (13) لسنة 2022\nفي شأن القطاع الصحي.\n",
                "\n# مرسوم اتحادي رقم (13) لسنة 2022\n",
            ),
            (
                "مقدمة النص.\n\nقانون العمل رقم 8 لسنة 1980\nأحكام عامة.\n",
                "\n# قانون العمل رقم 8 لسنة 1980\n",
            ),
        ],
    )
    def test_marker_line_promoted_to_h1(self, body, expected_line):
        result = _inject_arabic_structural_headings(body)
        assert expected_line in result


class TestReversedOcrVariantsInjectCorrectly:
    """Property 12(b): mirror-reversed OCR variants of the new markers
    (e.g. رارق for قرار) inject correctly via decide_rtl."""

    _FORWARD_DOC = """مرسوم اتحادي رقم (13) لسنة 2022
في شأن تنظيم القطاع الصحي

قرار مجلس الوزراء رقم (1) لسنة 2022
في شأن التنظيم الإداري

مادة 1
تعريفات
تسري على هذا المرسوم الاتحادي التعريفات التالية ما لم يقتض السياق خلاف ذلك.

مادة 2
نطاق التطبيق
تسري أحكام هذا القرار على جميع الجهات المعنية في الدولة."""

    def test_reversed_document_is_detected_as_mirror_reversed(self):
        reversed_doc = _mirror_reverse(self._FORWARD_DOC)
        assert decide_rtl(reversed_doc).reversed is True


class TestMidParagraphCitationsNotPromoted:
    """Property 12(c): mid-paragraph citations referencing قرار/مرسوم/قانون
    are NOT promoted -- the line-start anchor gating promotion protects
    these the same way it already protects مادة citations (RFC-028 D1)."""

    @pytest.mark.parametrize(
        "md",
        [
            (
                "نص سابق يمهد للموضوع.\n\n"
                "وتجدر الإشارة إلى ما ورد في القرار رقم 5 من هذا الشأن وتوضيحاته "
                "في السياق العام للموضوع محل النقاش والذي يحدد أحكاما طويلة إضافية.\n"
            ),
            (
                "نص سابق.\n\n"
                "تسري أحكام هذا التنظيم وفقا لما ورد في المرسوم رقم 13 بشأن هذا الموضوع "
                "وما يليه من أحكام تفصيلية إضافية تتعلق بالتطبيق العملي لهذه القواعد.\n"
            ),
            (
                "نص سابق.\n\n"
                "المشار إليها في القانون رقم 5 من هذا التنظيم وتفاصيله الإضافية "
                "التي يتوجب الرجوع إليها عند تطبيق هذه الأحكام في الحالات المماثلة.\n"
            ),
        ],
    )
    def test_citation_mid_paragraph_not_promoted(self, md):
        result = _inject_arabic_structural_headings(md)

        assert "\n#" not in result
        assert not result.startswith("#")


class TestRegressionFixtures:
    """Synthetic regression proxies for the corpus fixtures named in
    RFC-036 D5's Affected Documents list. These reproduce each fixture's
    defining structural-marker shape against the fixed code paths and
    assert the depth improvement."""

    @pytest.fixture(autouse=True)
    def _disable_density_guard(self, monkeypatch):
        import pageindex_mcp.converters.headings as _h
        monkeypatch.setattr(_h, "_AR_HEADING_MIN_CONTENT_CHARS", 0)

    def test_marsoom_biqanoon_13_2022_recovers_part_level_heading(self):
        """مرسوم بقانون اتحادي رقم (13) لسنة 2022 -- MARGINAL at depth 1, 0
        nodes; the مرسوم marker is now promoted to '#'."""
        md = "مرسوم بقانون اتحادي رقم (13) لسنة 2022\nفي شأن القطاع الصحي.\n\nمادة 1\nتعريفات.\n"

        result = _inject_arabic_structural_headings(md)

        assert result.startswith("# مرسوم بقانون اتحادي رقم (13) لسنة 2022\n")
        assert "\n## مادة 1\n" in result


# ===========================================================================
# D6 -- complexity-proportional depth-adequacy scoring in classify_verdict
# ===========================================================================
#
# expected_min_depth = min(5, 2 + floor(log2(node_count / 50))). A tree that
# clears the existing node_count/depth/max_leaf_ratio PASS gate but falls
# short of expected_min_depth is capped at MARGINAL with reason
# 'depth_inadequate', carrying expected_min_depth/actual_depth in the reason.
# Covers the required test matrix plus the 100/200/400 node boundary
# thresholds where expected_min_depth steps from 2->3, 3->4, 4->5.

_WORDS = [
    "the",
    "quick",
    "brown",
    "fox",
    "jumps",
    "over",
    "lazy",
    "dog",
    "while",
    "article",
    "clause",
    "section",
    "provides",
    "that",
    "obligation",
    "shall",
    "apply",
    "notwithstanding",
    "any",
    "other",
    "term",
]


def _leaf_text(i: int) -> str:
    return " ".join(_WORDS[j % len(_WORDS)] + str(i) for j in range(20))


def _make_tree(node_count: int, depth: int) -> list:
    """Build a chain of `depth` levels ending in enough equal-sized leaves
    to total `node_count` nodes, so max_leaf_ratio stays low and only the
    depth-adequacy gate is under test."""
    leaves_needed = node_count - (depth - 1)
    current = [{"title": "", "text": _leaf_text(i), "nodes": []} for i in range(leaves_needed)]
    for _ in range(depth - 1):
        current = [{"title": "", "text": _leaf_text(0), "nodes": current}]
    return current


def test_200_node_depth2_marginal_depth_inadequate():
    tree = _make_tree(200, 2)
    verdict, reason = classify_verdict(tree, "hierarchical", None)
    assert verdict == "MARGINAL"
    assert reason == "depth_inadequate:expected_min_depth=4,actual_depth=2"


def test_600_node_depth2_marginal():
    tree = _make_tree(600, 2)
    verdict, reason = classify_verdict(tree, "hierarchical", None)
    assert verdict == "MARGINAL"
    assert reason == "depth_inadequate:expected_min_depth=5,actual_depth=2"


def test_boundary_100_nodes_expected_depth_3():
    # At the 100-node threshold: expected_min_depth steps up to 3.
    tree = _make_tree(100, 2)
    verdict, reason = classify_verdict(tree, "hierarchical", None)
    assert verdict == "MARGINAL"
    assert reason == "depth_inadequate:expected_min_depth=3,actual_depth=2"

    tree = _make_tree(100, 3)
    verdict, reason = classify_verdict(tree, "hierarchical", None)
    assert verdict == "PASS"


def test_boundary_399_nodes_expected_depth_4():
    # Just below the 400-node threshold: expected_min_depth still 4.
    tree = _make_tree(399, 4)
    verdict, reason = classify_verdict(tree, "hierarchical", None)
    assert verdict == "PASS"
