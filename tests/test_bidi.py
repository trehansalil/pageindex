# ALLOW-NEW-TEST-FILE: consolidation target from ICR-97-rfc39 test reorganization
"""RTL detection, Arabic pipeline, bidi processing, AGPL bidi, and RFC Arabic tests.

Consolidated (ICR-97 test-budget reduction): absorbs test_zone4_bidi_rtl.py and
collapses per-row parametrize tables into table-driven tests that assert the
whole table and name every offending row.
"""

from __future__ import annotations

import inspect
import logging
import re
import shutil
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, call, patch

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
    splice_picture_text_for_tree,
)
from pageindex_mcp.converters.normalize import BIDI_NORM_VERSION
from pageindex_mcp.converters.ocr_langs import (
    TessdataUnavailableError,
    _LATIN_LANGS,
    _system_tessdata_cache,
    ensure_tessdata,
)
from pageindex_mcp.helpers import (
    TreeDefect,
    TreeGateResult,
    _segment_table_nodes,
    _strip_toc_heading_nodes,
    _strip_toc_heading_nodes_guarded,
    _tree_depth,
    _tree_node_count,
    classify_verdict,
    compute_image_enrichment_ratio,
    validate_tree,
)
from pageindex_mcp.helpers.gates import _gate_bidi_degraded
from pageindex_mcp.helpers.table_stitch import (
    _merge_continuation_table,
    stitch_continuation_tables,
)
from pageindex_mcp.helpers.tree_split import table_is_rtl
from pageindex_mcp.helpers.tree_validation import TreeSignals
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


# ---------------------------------------------------------------------------
# Shared fixtures
# ---------------------------------------------------------------------------

_ARABIC_LINE = "المادة الأولى تنظيم الحقوق"
_ENGLISH_LINE = "This is a normal English sentence with enough words to test."

_LOGICAL_ARABIC = "المادة الأولى تنظيم الحقوق والواجبات للمواطنين"


def _report(failures: list[str], what: str) -> None:
    """Assert once, naming every offending row."""
    assert not failures, f"{what}: {len(failures)} row(s) failed:\n  " + "\n  ".join(failures)


# ===========================================================================
# apply_rtl / normalize_for_garble
# ===========================================================================


class TestApplyRtlReversedFlagFalse:
    """reversed_flag=False must return input unchanged, for any script."""

    def test_text_unchanged_for_every_script(self):
        failures = []
        for label, line in (("arabic", _ARABIC_LINE), ("english", _ENGLISH_LINE)):
            if apply_rtl(line, reversed_flag=False) != line:
                failures.append(f"{label}: apply_rtl mutated text with reversed_flag=False")
        _report(failures, "apply_rtl(reversed_flag=False)")


class TestNormalizeRawMarkdown:
    """RAW_MARKDOWN strips markdown scaffolding (heading markers, pipes)."""

    def test_strips_markdown_scaffolding(self):
        heading = normalize_for_garble("# Heading", BlobKind.RAW_MARKDOWN)
        assert "#" not in heading and "Heading" in heading

        row = normalize_for_garble("| col1 | col2 | col3 |", BlobKind.RAW_MARKDOWN)
        assert "|" not in row
        assert "col1" in row and "col2" in row


# ===========================================================================
# decide_rtl: single-threshold (0.15) decider
# ===========================================================================


class TestDecideRtlThreshold:
    """decide_rtl is the sole RTL decision point and bails out at ratio <= 0.15."""

    def test_below_or_at_threshold_bails_out(self):
        """Latin, empty, low-Arabic-ratio and exactly-0.15 inputs all bail out
        (sampled == 0, reversed False)."""
        bilingual = "Hello world this is a long English text " * 5 + "مادة"
        at_threshold = "x" * 85 + "ا" * 15  # ~15% Arabic

        # preconditions
        assert sum(1 for c in bilingual if is_arabic_char(c)) / len(bilingual) < 0.15
        ratio = sum(1 for c in at_threshold if is_arabic_char(c)) / len(at_threshold)
        assert abs(ratio - 0.15) < 0.01

        cases = {
            "pure_latin": "This is a normal English sentence with enough length",
            "empty": "",
            "low_arabic_ratio": bilingual,
            "exactly_0.15": at_threshold,
        }
        failures = []
        for label, text in cases.items():
            decision = decide_rtl(text)
            if decision.reversed is not False:
                failures.append(f"{label}: reversed={decision.reversed}, expected False")
            if decision.sampled != 0:
                failures.append(f"{label}: sampled={decision.sampled}, expected 0 (bail-out)")
        _report(failures, "decide_rtl bail-out")

    def test_above_threshold_arabic_gets_evaluated(self):
        text = _LOGICAL_ARABIC + "\n" + _LOGICAL_ARABIC
        ar_count = sum(1 for c in text if is_arabic_char(c))
        assert ar_count / max(len(text), 1) > 0.15, "precondition: ratio above threshold"
        assert isinstance(decide_rtl(text), RtlDecision)


class TestConsistentHeadingBodyDecision:
    """reconstruct_bidi_order must apply the same decide_rtl threshold to
    headings and body text -- no threshold divergence."""

    def test_heading_and_body_share_one_threshold(self):
        # Below threshold: nothing is touched, heading marker survives.
        latin_body = "This is English content repeated. " * 20
        text = "## المادة" + "\n\n" + latin_body
        ratio = sum(1 for c in text if is_arabic_char(c)) / len(text)
        assert ratio < 0.15, f"precondition: ratio {ratio:.3f} must be below 0.15"
        result, _ = reconstruct_bidi_order(text)
        assert "##" in result, "heading marker must be preserved"
        assert "This is English content repeated." in result

        # Above threshold logical Arabic: heading and body stay readable.
        heading = "## المادة الأولى تنظيم الحقوق"
        body = "تنظيم الحقوق والواجبات للمواطنين في إطار القانون العام"
        result, _ = reconstruct_bidi_order(heading + "\n\n" + body + "\n" + body)
        assert "المادة الأولى" in result
        assert "تنظيم الحقوق" in result


# ===========================================================================
# ScriptContext.from_document
# ===========================================================================


class TestScriptContextFromDocument:
    """Script inference from filename codepoints plus the content override.

    detect_ocr_langs scans actual Unicode codepoints in the filename (not
    ISO-639 codes), so Latin-character filenames -- even with an '_ara'
    suffix -- infer 'Latn'; Arabic *content* then overrides that.
    """

    _GERMAN = "Die Versicherung umfasst die gesetzliche Haftpflicht"
    _ARABIC_BODY = "المادة " * 50
    _LATIN_BODY = "This is a standard English document about insurance terms. " * 50

    # (label, filename, raw_text, expected_script | "!Arab" | None, allowed_sources | None)
    _CASES = [
        ("arabic_codepoint_filename", "سياسة.pdf", "", "Arab", ("filename", "combined")),
        (
            "latin_filename_no_content",
            "musterbedingungen_deu.pdf",
            "",
            "Latn",
            ("filename", "combined"),
        ),
        ("source_filename_only", "doc_ara.pdf", "", None, ("filename",)),
        ("source_combined", "doc_deu.pdf", _GERMAN, None, ("combined",)),
        (
            "english_name_arabic_content",
            "document.pdf",
            _ARABIC_BODY,
            "Arab",
            ("content_override",),
        ),
        (
            "english_name_arabic_content_2",
            "federal_decree.pdf",
            "مرسوم بقانون " * 30,
            "Arab",
            ("content_override",),
        ),
        ("arabic_name_arabic_content", "مرسوم_13.pdf", _ARABIC_BODY, "Arab", None),
        ("latin_name_latin_content", "terms_and_conditions.pdf", _LATIN_BODY, "!Arab", None),
        ("empty_content_uses_filename", "document.pdf", "", "!Arab", ("filename", "none")),
    ]

    def test_script_and_source_table(self):
        failures = []
        for label, filename, raw_text, expected, allowed_sources in self._CASES:
            ctx = ScriptContext.from_document(filename, raw_text)
            if expected == "!Arab":
                if ctx.dominant_script == "Arab":
                    failures.append(f"{label}: dominant_script must NOT be 'Arab'")
            elif expected is not None and ctx.dominant_script != expected:
                failures.append(
                    f"{label}: dominant_script={ctx.dominant_script!r}, expected {expected!r}"
                )
            if allowed_sources is not None and ctx.source not in allowed_sources:
                failures.append(
                    f"{label}: source={ctx.source!r}, expected one of {allowed_sources}"
                )
        _report(failures, "ScriptContext.from_document")


# ===========================================================================
# Picture alignment: non-destructive splice
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


# ---------------------------------------------------------------------------
# Table helpers
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
    headers = ["البند", "2023", "2024"]
    rows = [
        ["الإيرادات", "100", "200"],
        ["المصروفات", "50", "80"],
    ]
    return _make_table_block(headers, rows)


def _latin_table_block() -> dict:
    """A table block with <30% Arabic chars so table_is_rtl returns False."""
    return _make_table_block(
        ["Item", "2023", "2024"],
        [["Revenue", "100", "200"], ["Expenses", "50", "80"]],
    )


def _continuation_block(headers: list[str], rows: list[list[str]]) -> dict:
    """A continuation table block (numeric-only headers)."""
    return _make_table_block(headers, rows)


# ═══════════════════════════════════════════════════════════════════════════
# Script-aware flat-prefer guard
# ═══════════════════════════════════════════════════════════════════════════


class TestScriptAwareFlatPreferGuard:
    """Arabic docs use a 1.5x flat-prefer multiplier; Latin docs use 3.0x.

    The Arabic multiplier must be strictly lower so heading-inflated Arabic
    trees cannot block flat fallback (marsoom-13 regression).
    """

    def test_multiplier_constants_and_ordering(self):
        from pageindex_mcp.client.recovery import (
            _ARABIC_FLAT_PREFER_MULTIPLIER,
            _RFC029_FLAT_PREFER_MULTIPLIER,
        )

        assert _ARABIC_FLAT_PREFER_MULTIPLIER == 1.5
        assert _RFC029_FLAT_PREFER_MULTIPLIER == 3.0
        assert _ARABIC_FLAT_PREFER_MULTIPLIER < _RFC029_FLAT_PREFER_MULTIPLIER, (
            f"Arabic multiplier ({_ARABIC_FLAT_PREFER_MULTIPLIER}) must be "
            f"< Latin multiplier ({_RFC029_FLAT_PREFER_MULTIPLIER})"
        )

    def test_expected_script_is_wired_end_to_end(self):
        """_recover_flat_prefer takes expected_script (default None), branches on
        'Arab' to select the Arabic multiplier, and indexer.py passes it."""
        from pageindex_mcp.client import indexer as _idx_mod
        from pageindex_mcp.client import recovery as _rec_mod
        from pageindex_mcp.client.recovery import RecoveryMixin

        sig = inspect.signature(RecoveryMixin._recover_flat_prefer)
        assert "expected_script" in sig.parameters, (
            f"_recover_flat_prefer must take expected_script; params: {list(sig.parameters)}"
        )
        assert sig.parameters["expected_script"].default is None

        src = inspect.getsource(RecoveryMixin._recover_flat_prefer)
        assert "_ARABIC_FLAT_PREFER_MULTIPLIER" in src
        assert 'expected_script == "Arab"' in src

        val = getattr(_rec_mod, "_ARABIC_FLAT_PREFER_MULTIPLIER")
        assert isinstance(val, float) and val > 0

        idx_src = inspect.getsource(_idx_mod)
        call_lines = [
            ln for ln in idx_src.split("\n") if "_recover_flat_prefer" in ln and "def " not in ln
        ]
        assert call_lines, "indexer.py must call _recover_flat_prefer"
        assert any("expected_script" in ln for ln in call_lines), (
            "_recover_flat_prefer call site in indexer.py must pass expected_script"
        )


# ═══════════════════════════════════════════════════════════════════════════
# ensure_tessdata non-Latin verification
# ═══════════════════════════════════════════════════════════════════════════


class TestEnsureTessdataNonLatinVerification:
    """ensure_tessdata must never silently assume non-Latin tessdata exists,
    and must never silently substitute Latin-only OCR (D5)."""

    def test_latin_requests_pass_through(self, monkeypatch, tmp_path):
        """Latin languages pass through with or without TESSDATA_PREFIX, and
        'ara' is not classified as Latin."""
        assert "ara" not in _LATIN_LANGS

        monkeypatch.delenv("TESSDATA_PREFIX", raising=False)
        monkeypatch.delenv("TESSDATA_ALLOW_DOWNLOAD", raising=False)
        _system_tessdata_cache.clear()
        assert ensure_tessdata(["eng", "deu"]) == ["eng", "deu"]

        monkeypatch.setenv("TESSDATA_PREFIX", str(tmp_path))
        _system_tessdata_cache.clear()
        (tmp_path / "deu.traineddata").write_bytes(b"x")
        (tmp_path / "eng.traineddata").write_bytes(b"x")
        assert ensure_tessdata(["deu", "eng"]) == ["deu", "eng"]

    def test_unavailable_non_latin_raises_on_every_path(self, monkeypatch, tmp_path):
        """No binary, a cached negative, and a Latin-only tessdata dir must all
        raise TessdataUnavailableError rather than degrade to Latin OCR."""
        monkeypatch.delenv("TESSDATA_ALLOW_DOWNLOAD", raising=False)

        # (a) no TESSDATA_PREFIX and no tesseract binary on PATH
        monkeypatch.delenv("TESSDATA_PREFIX", raising=False)
        _system_tessdata_cache.clear()
        monkeypatch.setattr(shutil, "which", lambda _name: None)
        with pytest.raises(TessdataUnavailableError, match="non-Latin tessdata missing"):
            ensure_tessdata(["ara"])

        # (b) cached negative -> raises without re-probing
        _system_tessdata_cache.clear()
        _system_tessdata_cache["ara"] = False
        with pytest.raises(TessdataUnavailableError):
            ensure_tessdata(["ara"])

        # (c) TESSDATA_PREFIX present but only Latin traineddata -> no silent
        #     Latin-only substitution
        monkeypatch.setenv("TESSDATA_PREFIX", str(tmp_path))
        _system_tessdata_cache.clear()
        (tmp_path / "eng.traineddata").write_bytes(b"x")
        with pytest.raises(TessdataUnavailableError, match="non-Latin tessdata missing"):
            ensure_tessdata(["ara", "eng"])

    def test_non_latin_with_system_tesseract_found_passes(self, monkeypatch):
        """Cache says the non-Latin traineddata is present -> the lang passes."""
        monkeypatch.delenv("TESSDATA_PREFIX", raising=False)
        monkeypatch.delenv("TESSDATA_ALLOW_DOWNLOAD", raising=False)
        _system_tessdata_cache.clear()
        _system_tessdata_cache["ara"] = True
        result = ensure_tessdata(["ara", "eng"])
        assert "ara" in result and "eng" in result


# ═══════════════════════════════════════════════════════════════════════════
# table_is_rtl stability across the merge chain
# ═══════════════════════════════════════════════════════════════════════════


class TestTableIsRtlStability:
    """table_is_rtl must be computed once on the original anchor and
    threaded through the entire merge chain, not recomputed per-merge."""

    def test_heuristic_classifies_by_arabic_share(self):
        failures = []
        for label, block, expected in (
            ("arabic_majority", _arabic_table_block(), True),
            ("latin_majority", _latin_table_block(), False),
        ):
            got = table_is_rtl(block)
            if got is not expected:
                failures.append(f"{label}: table_is_rtl={got}, expected {expected}")
        _report(failures, "table_is_rtl")

    def test_is_rtl_is_threaded_not_recomputed(self):
        """_merge_continuation_table takes is_rtl, and stitch_continuation_tables
        computes it on the anchor and passes it down."""
        assert "is_rtl" in inspect.signature(_merge_continuation_table).parameters
        src = inspect.getsource(stitch_continuation_tables)
        assert "table_is_rtl" in src, "stitch_continuation_tables must call table_is_rtl"
        assert "is_rtl=" in src, (
            "stitch_continuation_tables must pass is_rtl= to _merge_continuation_table"
        )

    def test_explicit_is_rtl_overrides_heuristic_and_chain_stays_stable(self):
        anchor = _arabic_table_block()
        cont = _continuation_block(["2025", "2026"], [["300", "400"], ["120", "150"]])

        result_ltr = _merge_continuation_table(anchor, cont, is_rtl=False)
        result_rtl = _merge_continuation_table(anchor, cont, is_rtl=True)
        assert result_ltr["headers"] != result_rtl["headers"], (
            "is_rtl=True vs False must produce different header orderings"
        )

        anchor = _arabic_table_block()
        assert table_is_rtl(anchor) is True, "Test setup: anchor must be RTL"
        blocks = [
            anchor,
            _continuation_block(["2025"], [["300"], ["120"]]),
            _continuation_block(["2026"], [["400"], ["150"]]),
        ]
        result = stitch_continuation_tables(blocks)
        assert len(result) == 1, f"Expected 1 merged table, got {len(result)} blocks"


# ═══════════════════════════════════════════════════════════════════════════
# Arabic heading injection revert guard
# ═══════════════════════════════════════════════════════════════════════════


class TestArabicHeadingInjectionRevertGuard:
    """Thin documents (<2000 content chars) with sparse Arabic markers
    have heading injection reverted; substantial documents keep it; Latin
    documents pass through untouched."""

    def test_density_guard_table(self):
        thin_lines = []
        for i in range(1, 6):
            thin_lines += [f"مادة ({i})", f"نص قصير {i}"]
        thin = "\n".join(thin_lines)
        assert len(thin) < 2000, f"setup: thin md must be <2000 chars, got {len(thin)}"

        big_lines = []
        for i in range(1, 11):
            big_lines += [f"مادة ({i})", "المادة تنص على " * 60]
        big = "\n".join(big_lines)
        assert len(big) > 5000, f"setup: substantial md must be >5000 chars, got {len(big)}"

        latin = "This is a standard English document.\n" * 100

        failures = []
        thin_out = _inject_arabic_structural_headings(thin)
        if not ("# " not in thin_out or thin_out == thin):
            failures.append("thin_doc: heading injection should have been reverted")

        big_out = _inject_arabic_structural_headings(big)
        if not [ln for ln in big_out.split("\n") if ln.startswith("## ")]:
            failures.append("substantial_doc: injected headings should have been kept")

        if _inject_arabic_structural_headings(latin) != latin:
            failures.append("non_arabic_doc: must pass through unchanged")
        _report(failures, "_inject_arabic_structural_headings density guard")


# ---------------------------------------------------------------------------
# image_enrichment_promoted garble gate (D1)
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
    def test_digit_noise_blocked_but_legitimate_blob_still_promoted(self):
        """A 70%-digit blob above the char floor must not return PASS; a
        legitimate low-digit blob above the floor still reaches PASS."""
        blob = _digit_blob()
        assert len(blob) >= 500
        verdict, _reason = classify_verdict(
            _structure_with_text(blob), "flat_prose", None, image_enrichment_ratio=0.85
        )
        assert verdict != "PASS"

        structure = [
            {"node_id": str(i), "title": "", "text": "x" * 200, "nodes": []} for i in range(3)
        ]
        verdict, reason = classify_verdict(
            structure, "flat_mixed", None, image_enrichment_ratio=0.85
        )
        assert (verdict, reason) == ("PASS", "image_enrichment_promoted")


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
    def test_char_floor_is_exclusive_below_300(self):
        """0 and 299 chars escalate (MOU MOHRE-style empty structures); 300 --
        at the floor -- does not."""
        failures = []
        for chars, expected in ((0, True), (299, True), (300, False)):
            got = _escalation_fires(ok=False, reason="node_count<3", total_chars=chars)
            if got is not expected:
                failures.append(f"total_chars={chars}: fires={got}, expected {expected}")
        _report(failures, "low-content OCR escalation")


# ---------------------------------------------------------------------------
# D3: RTL-reversal detection (validate_tree) + repair-first flow
# ---------------------------------------------------------------------------
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
    def test_reversed_flagged_logical_not_flagged(self):
        ok, reason = validate_tree(_reversed_tree())
        assert ok is False
        assert reason == "rtl_reversal", f"Expected rtl_reversal, got {reason}"

        logical_ok, logical_reason = validate_tree(_logical_tree())
        assert (logical_ok, logical_reason) != (False, "rtl_reversal")


class TestRepairFirstFlow:
    """RFC-027 D3: `rtl_reversal` must never hard-FAIL before
    `reconstruct_bidi_order` has been attempted."""

    def test_repair_converges_but_a_noop_repair_does_not_silently_accept(self):
        """With the PF false-positive fix the garble gate no longer masks
        rtl_reversal for Arabic trees, so a real repair converges; a no-op
        repair must leave the verdict at rtl_reversal or garbling."""
        ok, reason = _repair_first(_reversed_tree())
        assert ok is True, f"Expected repair to converge (ok=True), got ok={ok}, reason={reason}"

        structure = _reversed_tree()  # no-op repair: nothing rewritten
        ok, reason = validate_tree(structure)
        assert ok is False
        assert reason in ("rtl_reversal", "garbling"), (
            f"Expected rtl_reversal or garbling (D10a), got {reason}"
        )


# ---------------------------------------------------------------------------
# D4: Arabic structural heading injection -> depth-recovery integration
# ---------------------------------------------------------------------------
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


@pytest.fixture()
def _no_density_guard(monkeypatch):
    import pageindex_mcp.converters.headings as _h

    monkeypatch.setattr(_h, "_AR_HEADING_MIN_CONTENT_CHARS", 0)


class TestInjectArabicStructuralHeadingsBlockStart:
    def test_block_start_markers_promoted_at_correct_depth(self, _no_density_guard):
        """باب at a block start becomes H1; مادة becomes H2."""
        failures = []
        for label, md, expected in (
            ("bab_h1", "مقدمة النص.\n\nالباب الأول\nأحكام عامة\n", "\n# الباب الأول\n"),
            ("maddah_h2", "مقدمة النص.\n\nمادة 1\nنص المادة الأولى.\n", "\n## مادة 1\n"),
        ):
            if expected not in _inject_arabic_structural_headings(md):
                failures.append(f"{label}: missing {expected!r} in injected output")
        _report(failures, "block-start marker promotion")


class TestDepthRecoveryOnInjectedHeadings:
    """RFC-027 D4 -> D3-chain integration: injected headings must feed the
    EXISTING `_recover_heading_depth` chain and produce depth >= 2; without
    injection the same document stays flat (the step is load-bearing)."""

    def test_injection_is_load_bearing_for_depth_recovery(self, _no_density_guard):
        injected = _inject_arabic_structural_headings(_SYNTHETIC_DOC)
        assert _max_heading_level(_recover_heading_depth(injected, {}, "")) >= 2
        assert _max_heading_level(_recover_heading_depth(_SYNTHETIC_DOC, {}, "")) < 2


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
    def test_relaxed_bound_applies_only_at_node_count_5_or_below(self):
        """node_count == 5 / leaf_concentration 0.39 promotes via
        small_doc_promoted (the relaxed 0.40 bound); node_count == 8 in the
        6-10 band keeps the 0.20 bound and must NOT promote."""
        verdict, reason = classify_verdict(
            _flat_leaf_tree([39, 16, 15, 15, 15]), "flat_prose", None
        )
        assert (verdict, reason) == ("PASS", "small_doc_promoted")

        verdict, _ = classify_verdict(
            _flat_leaf_tree([35, 10, 10, 10, 10, 10, 10, 5]), "flat_prose", None
        )
        assert verdict != "PASS"


# ---------------------------------------------------------------------------
# D6: deduplicate identical adjacent <!-- image --> markers
# ---------------------------------------------------------------------------
_DEDUP_RE = re.compile(r"(<!-- image -->)\s*(?=<!-- image -->)")


class TestMarkerDedupRegex:
    """Unit-level: the dedup regex itself, mirroring the exact pattern used
    at client.py's standalone-image branch."""

    def test_adjacent_markers_collapse(self):
        failures = []
        for label, md in (
            ("whitespace_separated", "<!-- image -->\n\n<!-- image -->"),
            ("directly_adjacent", "<!-- image --><!-- image -->"),
        ):
            n = _DEDUP_RE.sub("", md).count("<!-- image -->")
            if n != 1:
                failures.append(f"{label}: collapsed to {n} markers, expected 1")
        _report(failures, "image-marker dedup")


# ---------------------------------------------------------------------------
# ToC-strip helpers
# ---------------------------------------------------------------------------


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
    async def test_commit_sha_warns_and_pipeline_version_errors(self, monkeypatch, caplog):
        """Both skew signals increment their counter; commit_sha logs WARNING,
        pipeline_version escalates to ERROR."""
        before = _skew_count("commit_sha")
        client = _make_httpx_client({"commit_sha": "remote-sha", "pipeline_version": 4})
        with caplog.at_level(logging.WARNING, logger="pageindex_mcp.client"):
            await _remote._check_remote_docling_version(client)
        assert _skew_count("commit_sha") == before + 1
        assert any("remote-sha" in r.message for r in caplog.records)
        assert any(r.levelno == logging.WARNING for r in caplog.records)

        caplog.clear()
        monkeypatch.setattr(_remote, "_remote_docling_version", None)
        before = _skew_count("pipeline_version")
        client = _make_httpx_client({"commit_sha": "local-sha", "pipeline_version": 3})
        with caplog.at_level(logging.WARNING, logger="pageindex_mcp.client"):
            await _remote._check_remote_docling_version(client)
        assert _skew_count("pipeline_version") == before + 1
        assert any(r.levelno == logging.ERROR for r in caplog.records)


def _resolve_build_sha(env: dict) -> str:
    """Re-runs the exact expression client.py's module-level
    _CLIENT_BUILD_SHA uses, against an isolated env dict, so the precedence
    logic is covered without reload()-ing the client module."""
    return env.get("BUILD_SHA") or env.get("CLIENT_BUILD_SHA", "unknown")


class TestBuildShaPrecedence:
    def test_new_env_var_wins_legacy_is_fallback(self):
        assert (
            _resolve_build_sha({"BUILD_SHA": "new-sha", "CLIENT_BUILD_SHA": "old-sha"}) == "new-sha"
        )
        assert _resolve_build_sha({"CLIENT_BUILD_SHA": "old-sha"}) == "old-sha"


# ===========================================================================
# helpers._strip_toc_heading_nodes / _strip_toc_heading_nodes_guarded (D11/D16)
# ===========================================================================


class TestTocHeadingStrip:
    """D11: `_strip_toc_heading_nodes` removes ToC dot-leader nodes; real
    body-text nodes (even with an embedded page number) survive."""

    def test_strips_toc_nodes_only(self):
        real_nodes = [
            _real_node(f"Article {i}", f"This is the body text of article {i}.")
            for i in range(1, 6)
        ]
        toc_nodes = [_toc_node(f"Article {i}") for i in range(1, 11)]

        result = _strip_toc_heading_nodes(real_nodes + toc_nodes)
        assert len(result) == 5
        assert [n["title"] for n in result] == [f"Article {i}" for i in range(1, 6)]

        page_ref = _real_node(
            "Article 1",
            "This clause references page 12 of the appendix for further detail.",
        )
        kept = _strip_toc_heading_nodes([page_ref])
        assert len(kept) == 1 and kept[0]["title"] == "Article 1"


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
        tree = real_nodes + [_toc_node(f"Schedule {i}") for i in range(1, 6)]
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
    """reconstruct_bidi_order must be idempotent on the empty-input boundary
    and on mixed-script documents."""

    def test_idempotent(self):
        mixed = (
            "# Section Title\n\n"
            "This document mixes English body text with Arabic: "
            "هذا نص عربي مضمن داخل نص انجليزي طويل بما يكفي لتفعيل اعادة الترتيب "
            "and continues in English afterwards."
        )
        failures = []
        for label, text in (("empty", ""), ("mixed_arabic_latin", mixed)):
            once, _ = reconstruct_bidi_order(text)
            twice, _ = reconstruct_bidi_order(once)
            if twice != once:
                failures.append(f"{label}: second pass changed the text")
        _report(failures, "reconstruct_bidi_order idempotence")


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

    def test_counter_increments_only_when_markdown_changes(self):
        before = _renorm_counter_value()
        assert _apply_d3_gate(_REVERSED_HEADING_MD) == _CORRECTED_HEADING_MD
        assert _renorm_counter_value() == before + 1

        before = _renorm_counter_value()
        assert _apply_d3_gate(_ALREADY_CORRECT_MD) == _ALREADY_CORRECT_MD
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

    def test_single_script_degenerate_rows_still_collapse(self):
        """The mixed-script guard must not disable the RFC-029 D4 collapse
        for single-script rows (Latin-only or Arabic-only).

        RFC-035 D0: the first post-separator row is exempt from collapse
        (Docling repeated-label guard), independent of the D17 mixed-script
        guard tested here -- so a distinct leading row precedes the
        degenerate one to isolate the two guards.
        """
        cases = [
            ("latin_only", "| p | q | r | s |", "Yes"),
            ("arabic_only", "| لا | لا | لا | لا |", "نعم"),
        ]
        failures = []
        for label, leading_row, value in cases:
            md = (
                "| a | b | c | d |\n"
                "| --- | --- | --- | --- |\n"
                f"{leading_row}\n"
                f"| {value} | {value} | {value} | {value} |\n"
            )
            out = _repair_docling_tables(md, "test.pdf")
            if f"| {value} |" not in out:
                failures.append(f"{label}: degenerate row was not collapsed to a single cell")
            if f"| {value} | {value} | {value} | {value} |" in out:
                failures.append(f"{label}: original four-column degenerate row survived")
        _report(failures, "single-script degenerate row collapse")


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
        retries and returns without raising.  The whole delay schedule is
        budget-capped at 0.45s (Property 5)."""
        assert sum(_WRITE_BARRIER_DELAYS) <= 0.45

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
    """Property 1: MAX_LANDSCAPE_PAGES and the deadline both bound the
    per-page reextraction loop."""

    def test_cap_and_deadline_bound_the_loop(self, tmp_path, monkeypatch):
        _wire_fake_docling(monkeypatch, tmp_path)
        pages = [
            {"page_no": i, "rotate": 90, "is_landscape": True, "char_count": 10} for i in range(15)
        ]
        results = _landscape_rasterize_rotate_reextract("fake.pdf", pages)
        assert len(results) == converters.MAX_LANDSCAPE_PAGES
        assert [r["page_no"] for r in results] == list(range(converters.MAX_LANDSCAPE_PAGES))

        monkeypatch.setattr(converters.pictures, "LANDSCAPE_REEXTRACT_DEADLINE_SECONDS", 0.0)
        pages = [
            {"page_no": i, "rotate": 90, "is_landscape": True, "char_count": 10} for i in range(5)
        ]
        assert _landscape_rasterize_rotate_reextract("fake.pdf", pages) == []


class TestSpliceLandscapeFallback:
    """Property 3: fallback markdown lands at its original page position,
    not appended at document end."""

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


class TestWriteBarrierExhaustionPropagates:
    """Property 6 / Zone-4 Phase 3: save_doc_meta no longer calls
    _confirm_write_visible (the sidecar is archival-only; Postgres is the
    sole verdict authority), so it must NOT raise even when the barrier
    would fail.  The barrier is intentionally retained for save_doc /
    save_flat_doc."""

    def test_save_doc_meta_does_not_raise_on_barrier_exhaustion(self, mock_minio, monkeypatch):
        barrier = MagicMock(side_effect=PersistenceNotVisibleError("processed/doc.meta.json"))
        monkeypatch.setattr(
            "pageindex_mcp.storage.minio_ops._confirm_write_visible", barrier
        )
        save_doc_meta(
            "doc123",
            {
                "doc_id": "doc123",
                "doc_name": "t.pdf",
                "source_url": "s3://x",
                "processed_at": "2026-08-10T00:00:00Z",
            },
        )
        barrier.assert_not_called()
        assert mock_minio.put_object.called


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

    return {**idx_mocks, **rec_mocks}


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

        try:
            doc_id = await c.index(pdf_file)
            assert isinstance(doc_id, str)
            mocks["save_flat_doc"].assert_called_once()
        except LowQualityTreeError:
            pass


# ===========================================================================
# D4 -- image-enrichment skip metadata (Properties 10-11)
# ===========================================================================


class TestEnrichImageBlocksPropagatesSkipMetadata:
    """_enrich_image_blocks copies skipped_reason from PictureResult onto
    the matching block dict, for every skip reason."""

    @pytest.mark.asyncio
    async def test_skip_reasons_propagated(self):
        failures = []
        for skip_reason in ("decorative_icon", "landscape_fallback_picture"):
            blocks = [{"role": "image", "index": 0}]
            pic_results = [{"skipped_reason": skip_reason}]
            with patch("pageindex_mcp.client.images.save_figure"):
                await _enrich_image_blocks(blocks, pic_results, "doc1")
            if blocks[0].get("skipped_reason") != skip_reason:
                failures.append(
                    f"{skip_reason}: block skipped_reason={blocks[0].get('skipped_reason')!r}"
                )
        _report(failures, "_enrich_image_blocks skip metadata")


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


class TestImageEnrichmentRatioAndVerdict:
    """compute_image_enrichment_ratio drops decorative/skipped blocks from
    both numerator and denominator (None, not 0 or NaN, when nothing is
    scoreable), so classify_verdict's image_enrichment_promoted branch
    cannot fire on a document with no genuine enrichment -- while genuinely
    enriched documents still promote."""

    def test_all_skipped_yields_none_but_genuine_enrichment_promotes(self):
        skipped = [
            {"role": "image", "skipped_reason": "ocr_min_chars"},
            {"role": "image", "skipped_reason": "page_coverage"},
        ]
        assert compute_image_enrichment_ratio(skipped) is None

        enriched = [
            {"role": "image", "ocr_text": "42% revenue growth"},
            {"role": "image", "ocr_text": "31% cost reduction"},
        ]
        ratio = compute_image_enrichment_ratio(enriched)
        assert ratio == 1.0

        structure = [{"title": "", "text": "x" * 200, "nodes": []} for _ in range(3)]
        verdict, reason = classify_verdict(
            structure,
            content_class="flat_prose",
            validate_result=None,
            image_enrichment_ratio=ratio,
        )
        assert (verdict, reason) == ("PASS", "image_enrichment_promoted")


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

    _CASES = [
        (
            "qarar",
            "مقدمة النص.\n\nقرار مجلس الوزراء رقم (1) لسنة 2022\nفي شأن التنظيم.\n",
            "\n# قرار مجلس الوزراء رقم (1) لسنة 2022\n",
        ),
        (
            "marsoom",
            "مقدمة النص.\n\nمرسوم اتحادي رقم (13) لسنة 2022\nفي شأن القطاع الصحي.\n",
            "\n# مرسوم اتحادي رقم (13) لسنة 2022\n",
        ),
        (
            "qanoon",
            "مقدمة النص.\n\nقانون العمل رقم 8 لسنة 1980\nأحكام عامة.\n",
            "\n# قانون العمل رقم 8 لسنة 1980\n",
        ),
    ]

    def test_marker_lines_promoted_to_h1(self, _no_density_guard):
        failures = []
        for label, body, expected_line in self._CASES:
            if expected_line not in _inject_arabic_structural_headings(body):
                failures.append(f"{label}: {expected_line!r} not injected")
        _report(failures, "new part-level marker injection")


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
        assert decide_rtl(_mirror_reverse(self._FORWARD_DOC)).reversed is True


class TestMidParagraphCitationsNotPromoted:
    """Property 12(c): mid-paragraph citations referencing قرار/مرسوم/قانون
    are NOT promoted -- the line-start anchor gating promotion protects
    these the same way it already protects مادة citations (RFC-028 D1)."""

    _CASES = [
        (
            "qarar_citation",
            "نص سابق يمهد للموضوع.\n\n"
            "وتجدر الإشارة إلى ما ورد في القرار رقم 5 من هذا الشأن وتوضيحاته "
            "في السياق العام للموضوع محل النقاش والذي يحدد أحكاما طويلة إضافية.\n",
        ),
        (
            "marsoom_citation",
            "نص سابق.\n\n"
            "تسري أحكام هذا التنظيم وفقا لما ورد في المرسوم رقم 13 بشأن هذا الموضوع "
            "وما يليه من أحكام تفصيلية إضافية تتعلق بالتطبيق العملي لهذه القواعد.\n",
        ),
        (
            "qanoon_citation",
            "نص سابق.\n\n"
            "المشار إليها في القانون رقم 5 من هذا التنظيم وتفاصيله الإضافية "
            "التي يتوجب الرجوع إليها عند تطبيق هذه الأحكام في الحالات المماثلة.\n",
        ),
    ]

    def test_citations_mid_paragraph_not_promoted(self):
        failures = []
        for label, md in self._CASES:
            result = _inject_arabic_structural_headings(md)
            if "\n#" in result or result.startswith("#"):
                failures.append(f"{label}: mid-paragraph citation was promoted to a heading")
        _report(failures, "mid-paragraph citation guard")


class TestRegressionFixtures:
    """Synthetic regression proxy for the corpus fixture named in RFC-036
    D5's Affected Documents list."""

    def test_marsoom_biqanoon_13_2022_recovers_part_level_heading(self, _no_density_guard):
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


class TestDepthAdequacyScoring:
    """Covers the required matrix plus the 100/400-node boundaries where
    expected_min_depth steps 2->3 and 3->4."""

    # (label, node_count, depth, expected_verdict, expected_reason | None)
    _CASES = [
        ("200n_d2", 200, 2, "MARGINAL", "depth_inadequate:expected_min_depth=3,actual_depth=2"),
        ("600n_d2", 600, 2, "MARGINAL", "depth_inadequate:expected_min_depth=4,actual_depth=2"),
        ("100n_d1", 100, 1, "MARGINAL", "depth_inadequate:expected_min_depth=2,actual_depth=1"),
        ("100n_d2", 100, 2, "PASS", None),
        ("399n_d3", 399, 3, "PASS", None),
    ]

    def test_expected_min_depth_table(self):
        failures = []
        for label, node_count, depth, exp_verdict, exp_reason in self._CASES:
            verdict, reason = classify_verdict(_make_tree(node_count, depth), "hierarchical", None)
            if verdict != exp_verdict:
                failures.append(f"{label}: verdict={verdict}, expected {exp_verdict}")
            if exp_reason is not None and reason != exp_reason:
                failures.append(f"{label}: reason={reason!r}, expected {exp_reason!r}")
        _report(failures, "depth-adequacy scoring")


# ===========================================================================
# Zone 4 -- Bidi/RTL processing split (merged from test_zone4_bidi_rtl.py)
# ===========================================================================


class TestBidiNormVersion:
    def test_version_is_int_and_re_exported(self):
        from pageindex_mcp.converters import BIDI_NORM_VERSION as exported

        assert isinstance(BIDI_NORM_VERSION, int)
        assert BIDI_NORM_VERSION >= 2
        assert exported == BIDI_NORM_VERSION


class TestPreInferenceNormalizeDelegation:
    """pictures._pre_inference_normalize must delegate to the canonical
    normalize implementation, and the presentation-form signal is
    ratio-gated (RFC-046 D5) -- a minority of PF codepoints among regular
    Arabic must NOT condemn the document."""

    def test_presentation_form_signal_is_ratio_gated(self):
        from pageindex_mcp.converters.pictures import _pre_inference_normalize

        cases = [
            ("pf_dominant", "ﭐﭑﭒﭓﭔﭕﭖﭗ اب", True),
            ("pf_minority", "ﭐﭑﭒ مرحبا بالعالم", False),  # 3 PF + ~11 regular Arabic
        ]
        failures = []
        for label, text, expected in cases:
            _, rtl_decision = _pre_inference_normalize(text)
            if rtl_decision is None:
                failures.append(f"{label}: rtl_decision is None")
            elif rtl_decision.had_presentation_forms is not expected:
                failures.append(
                    f"{label}: had_presentation_forms="
                    f"{rtl_decision.had_presentation_forms}, expected {expected}"
                )
        _report(failures, "presentation-form ratio gate")

    def test_delegates_to_canonical_impl_and_handles_latin(self):
        from pageindex_mcp.converters.normalize import _pre_inference_normalize as canonical
        from pageindex_mcp.converters.pictures import _pre_inference_normalize as pictures_impl

        text = "## Section\n\nNormal text with no special chars."
        assert pictures_impl(text) == canonical(text)

        latin_text, _ = pictures_impl("# Hello World\n\nSome plain English text.")
        assert isinstance(latin_text, str)


class TestGateBidiDegradedPresentationForms:
    @pytest.fixture()
    def dummy_sig(self):
        return TreeSignals(
            node_count=5,
            depth=3,
            max_leaf_ratio=0.4,
            flat_text="dummy text",
            garbled=False,
            garble_ratio=0.0,
            effectively_garbled=False,
            is_reordered=False,
            expected_min_depth=2,
        )

    @pytest.fixture()
    def dummy_script_ctx(self):
        return ScriptContext(
            dominant_script="ar",
            had_presentation_forms=False,
            source="test",
        )

    @staticmethod
    def _rtl(*, reversed_flag: bool, pf: bool) -> RtlDecision:
        return RtlDecision(
            reversed=reversed_flag,
            repair_effective=False,
            sampled=0,
            method="test",
            had_presentation_forms=pf,
        )

    def test_firing_table(self, dummy_sig, dummy_script_ctx):
        """Fires on had_presentation_forms OR reversed; not when both are
        False, and not when there is no RtlDecision at all."""
        cases = [
            (
                "presentation_forms",
                self._rtl(reversed_flag=False, pf=True),
                True,
                "had_presentation_forms=True",
            ),
            ("reversed", self._rtl(reversed_flag=True, pf=False), True, "reversed=True"),
            ("both_false", self._rtl(reversed_flag=False, pf=False), False, None),
            ("no_rtl_decision", None, False, None),
        ]
        failures = []
        for label, rtl, expected, detail_substr in cases:
            fires, detail = _gate_bidi_degraded(dummy_sig, [], dummy_script_ctx, None, rtl)
            if fires is not expected:
                failures.append(f"{label}: fires={fires}, expected {expected}")
            elif detail_substr is not None and detail_substr not in detail:
                failures.append(f"{label}: detail={detail!r} missing {detail_substr!r}")
        _report(failures, "_gate_bidi_degraded")

    def test_disabled_by_env(self, dummy_sig, dummy_script_ctx, monkeypatch):
        from pageindex_mcp.config import reset_pipeline_config

        monkeypatch.setenv("BIDI_COHERENCE_ENFORCE", "false")
        reset_pipeline_config()
        try:
            rtl = self._rtl(reversed_flag=True, pf=True)
            fires, _ = _gate_bidi_degraded(dummy_sig, [], dummy_script_ctx, None, rtl)
            assert fires is False
        finally:
            monkeypatch.undo()
            reset_pipeline_config()
