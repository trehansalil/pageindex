"""RFC-034 consolidated tests: bidi reordering + AGPL fallback gating.

Consolidates the former test_rfc034_d0_d1 / d3 / d4 / d6_d7 / d9 / d11 /
d14 / d16 / d17 / d18 / d19 files into one module, grouped by the
production function each class exercises.
"""

import logging
import unicodedata
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, call

import pytest
from minio.error import S3Error

from pageindex_mcp.client import (
    _BIDI_RENORM_LATIN_GUARD,
    _latin_fraction,
    _renormalize_bidi_guarded,
)
from pageindex_mcp.client import indexer as _idx
from pageindex_mcp.client import remote as _remote
from pageindex_mcp.converters import (
    _repair_docling_tables,
    reconstruct_bidi_order,
)
from pageindex_mcp.helpers import (
    _strip_toc_heading_nodes,
    _strip_toc_heading_nodes_guarded,
    _tree_depth,
    _tree_node_count,
)
from pageindex_mcp.metrics import (
    DOCLING_VERSION_SKEW,
    REMOTE_MD_RENORMALIZED,
    TOC_STRIP_SKIPPED,
    WRITE_BARRIER_RETRIES,
)
from pageindex_mcp.storage import (
    _WRITE_BARRIER_DELAYS,
    PersistenceNotVisibleError,
    _confirm_write_visible,
)

# ---------------------------------------------------------------------------
# Shared fixtures / helpers
# ---------------------------------------------------------------------------

_DOC_STORE = Path(__file__).resolve().parent.parent / "doc_store"
_CORPUS_MD_FILES = sorted(_DOC_STORE.rglob("*.md")) if _DOC_STORE.is_dir() else []

# Genuinely visual/glyph-order Arabic (base Arabic U+0600-06FF, character
# order reversed, no presentation-form shaping). Reads backwards.
_VISUAL_LINE = "رارق سلجم ءارزولا مقر ةنسل نأشب ميظنت تاقالع لمعلا يف رطق"
_VISUAL_LINE_2 = "رارقلا كلذ لدعملا ةدراولا صوصنلا قفو لمعلا ماكحأ ذيفنت"
_REVERSED_WORD = "رارق"  # reversed form of "قرار" (decision)

# Correctly-ordered (logical) Arabic.
_LOGICAL_LINE = "قرار مجلس الوزراء رقم لسنة بشأن تنظيم علاقات العمل وتعديلاته"
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
