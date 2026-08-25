"""RFC-035 inspector tests (D0/D1/D2), consolidated.

D0: prev_was_separator guard on _repair_docling_tables — collapse fires on
    a row iff all cells are byte-identical, the cell count exceeds
    _RFC029_TABLE_MIN_COLLAPSE_COLS, AND the row does not immediately
    follow a separator row (|---|).

D1: inspector_class threading through classify_verdict's cat_c branch —
    for tree-path docs (content_class empty/falsy), inspector_class ==
    "text_based" widens the cat_c promotion threshold by 1.2x;
    content_class remains the sole cat_a/cat_b/cat_c branch selector.

D2: landscape orientation detection and rasterize-rotate-reextract
    fallback — tagging, threshold-gated triggering, rasterization
    failure fallthrough, and post-reextraction routing re-evaluation.
"""

import logging
import random
import string
from dataclasses import replace
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.conftest import filler_text

pytest.importorskip("fitz")
import fitz

from pageindex_mcp import converters
from pageindex_mcp.client import CustomPageIndexClient
from pageindex_mcp.client import images as _img
from pageindex_mcp.client import indexer as _idx
from pageindex_mcp.client import recovery as _rec
from pageindex_mcp.converters import (
    _RFC029_TABLE_MIN_COLLAPSE_COLS,
    _landscape_pages_below_threshold,
    _landscape_rasterize_rotate_reextract,
    _repair_docling_tables,
    _tag_landscape_pages_for_fallback,
)
from pageindex_mcp.helpers import GarbleReport, classify_verdict
from pageindex_mcp.helpers.gates import TreeDefect, TreeSignals
from pageindex_mcp.helpers.types import TreeGateResult

_THRESHOLD = _RFC029_TABLE_MIN_COLLAPSE_COLS
_TRIALS = 60
_SEED = 20260810


# ---------------------------------------------------------------------------
# D0: _repair_docling_tables separator guard
# ---------------------------------------------------------------------------


def _collapsed_rows_logged(caplog) -> int:
    for record in caplog.records:
        message = record.getMessage()
        if "table_repair" in message and "collapsed_rows=" in message:
            marker = "collapsed_rows="
            start = message.index(marker) + len(marker)
            end = message.index(",", start)
            return int(message[start:end])
    raise AssertionError("no table_repair log record found")


class TestTableRepairSeparatorGuard:
    def test_first_post_separator_degenerate_row_is_preserved(self, caplog):
        """Row immediately after separator with all-identical cells (count >
        threshold) is a Docling repeated-label first body row, not a merge
        artefact -- must be preserved in normalized minimal-padding form and
        collapsed_rows must be 0."""
        md = (
            "| A | B | C | D |\n"
            "| --- | --- | --- | --- |\n"
            "| Fee     | Fee     | Fee     | Fee     |\n"
        )
        with caplog.at_level(logging.INFO):
            out = _repair_docling_tables(md, "cabinet_resolution_no_21.pdf")
        lines = out.strip().split("\n")
        assert lines[-1] == "| Fee | Fee | Fee | Fee |"
        assert "| Fee |" not in lines
        assert _collapsed_rows_logged(caplog) == 0

    def test_guard_scope_is_limited_to_a_single_first_row(self):
        """Scope-limitation verification, combining two related scenarios:

        (a) when the first AND second post-separator rows are both
            degenerate, only the first is guarded -- the second is
            collapsed (the guard shields a single row only);
        (b) the prev_was_separator flag resets to False after the first
            post-separator row is processed -- a degenerate row at
            position 3+ (after a normal row) must still be collapsed.
        """
        md_two_consecutive = (
            "| A | B | C | D |\n"
            "| --- | --- | --- | --- |\n"
            "| Fee | Fee | Fee | Fee |\n"
            "| dup | dup | dup | dup |\n"
        )
        out = _repair_docling_tables(md_two_consecutive, "cabinet_resolution_no_21.pdf")
        lines = out.strip().split("\n")
        assert "| Fee | Fee | Fee | Fee |" in lines
        assert lines[-1] == "| dup |"
        assert "| dup | dup | dup | dup |" not in out

        md_flag_reset = (
            "| A | B | C | D |\n"
            "| --- | --- | --- | --- |\n"
            "| Fee | Fee | Fee | Fee |\n"
            "| w | x | y | z |\n"
            "| dup | dup | dup | dup |\n"
        )
        out = _repair_docling_tables(md_flag_reset, "cabinet_resolution_no_21.pdf")
        lines = out.strip().split("\n")
        assert "| Fee | Fee | Fee | Fee |" in lines
        assert "| w | x | y | z |" in lines
        assert lines[-1] == "| dup |"
        assert "| dup | dup | dup | dup |" not in out

    def test_collapse_requires_all_three_conditions_simultaneously(self):
        """Generalized property test: collapse fires on a row iff (a) every
        cell is byte-identical, (b) cell count exceeds the collapse
        threshold, AND (c) the row does not immediately follow a separator
        row."""

        def _random_word(rng: random.Random) -> str:
            return "".join(rng.choices(string.ascii_lowercase, k=rng.randint(1, 6)))

        def _random_row(rng: random.Random, num_cols: int, identical: bool) -> list[str]:
            if identical:
                word = _random_word(rng)
                return [word] * num_cols
            cells = [_random_word(rng) for _ in range(num_cols)]
            if len(set(cells)) == 1:
                cells[0] = cells[0] + "x"
            return cells

        def _build_table(target_row: list[str], first_post_separator: bool) -> str:
            header = ["h" + str(i) for i in range(len(target_row))]
            lines = ["| " + " | ".join(header) + " |"]
            lines.append("| " + " | ".join("---" for _ in header) + " |")
            if not first_post_separator:
                filler = ["f" + str(i) for i in range(len(target_row))]
                lines.append("| " + " | ".join(filler) + " |")
            lines.append("| " + " | ".join(target_row) + " |")
            return "\n".join(lines) + "\n"

        rng = random.Random(_SEED)

        for _ in range(_TRIALS):
            num_cols = rng.randint(2, 8)
            identical = rng.choice([True, False])
            first_post_separator = rng.choice([True, False])

            target_row = _random_row(rng, num_cols, identical)
            md = _build_table(target_row, first_post_separator)
            out = _repair_docling_tables(md, "prop.pdf")

            all_identical = len(set(target_row)) == 1
            over_threshold = num_cols > _THRESHOLD
            should_collapse = all_identical and over_threshold and not first_post_separator

            collapsed_line = "| " + target_row[0] + " |"
            full_line = "| " + " | ".join(target_row) + " |"

            if should_collapse:
                assert collapsed_line in out.split("\n"), (
                    f"expected collapse: cols={num_cols} identical={identical} "
                    f"first_post_sep={first_post_separator}\n{md}\n---\n{out}"
                )
                assert full_line not in out.split("\n")
            else:
                assert full_line in out.split("\n"), (
                    f"expected preservation: cols={num_cols} identical={identical} "
                    f"first_post_sep={first_post_separator}\n{md}\n---\n{out}"
                )


# ---------------------------------------------------------------------------
# D1: inspector_class threading through classify_verdict
# ---------------------------------------------------------------------------


def _flat_leaf_tree(chars_per_leaf: list[int]) -> list[dict]:
    """A flat (depth == 1) sibling tree with one leaf per entry in
    ``chars_per_leaf``, using prose-shaped filler so improved garble
    detection does not flag test fixtures."""
    return [
        {"node_id": str(i), "title": "", "text": filler_text(n, i), "nodes": []}
        for i, n in enumerate(chars_per_leaf)
    ]


class TestInspectorClassThreading:
    def test_empty_content_class_text_based_inspector_promotes_cat_c(self):
        """content_class='', inspector_class='text_based': leaf_concentration
        0.20 exceeds the default cat_c threshold (0.17) but clears the
        widened 0.204 (0.17 * 1.2) threshold -- promote cat_c_promoted."""
        structure = _flat_leaf_tree([20, 20, 20, 20, 20])
        verdict, reason = classify_verdict(structure, "", None, inspector_class="text_based")
        assert verdict == "PASS"
        assert reason in ("", "cat_c_promoted")

    def test_flat_mixed_content_class_takes_precedence_over_inspector_class(self):
        """content_class='flat_mixed' with inspector_class='text_based':
        content_class remains the sole branch selector, so this takes the
        flat_/cat_b branch (not cat_c) regardless of inspector_class."""
        structure = _flat_leaf_tree([60] * 10)
        verdict, reason = classify_verdict(
            structure, "flat_mixed", None, inspector_class="text_based"
        )
        assert verdict == "PASS"
        assert reason in ("", "cat_b_promoted")

    def test_empty_content_class_cat_c_threshold_boundary(self):
        """Positive and negative boundary checks combined, both with
        inspector_class=None (omitted default) -- pre-D1 behavior:

        - leaf_concentration ~0.143 clears the unwidened default 0.17
          cat_c threshold (backward compat, promoted);
        - leaf_concentration 0.20 (above 0.17, below the widened 0.204)
          must NOT promote -- proves D1 *widens* the threshold
          conditionally rather than raising it unconditionally.
        """
        structure_below = _flat_leaf_tree([20] * 7)
        verdict, reason = classify_verdict(structure_below, "", None)
        assert verdict == "PASS"
        assert reason in ("", "cat_c_promoted")

        structure_boundary = _flat_leaf_tree([20] * 5)
        sig = TreeSignals.from_tree(structure_boundary, garble_threshold=0.15)
        gate = TreeGateResult(
            ok=False,
            defect=TreeDefect.DEPTH_LOW,
            detail="depth=1",
            signals=sig,
            all_defects=frozenset({TreeDefect.DEPTH_LOW}),
        )
        verdict, reason = classify_verdict(structure_boundary, "", gate)
        assert (verdict, reason) == ("MARGINAL", "depth=1")


class TestInspectorClassPrecedenceProperty:
    """Property-based test (D1): random (content_class, inspector_class)
    pairs -- content_class always takes routing precedence; inspector_class
    only influences the cat_c branch."""

    _CONTENT_CLASSES = ["ocr_scanned", "ocr_image", "flat_prose", "flat_mixed"]
    _INSPECTOR_CLASSES = [None, "", "text_based", "scanned", "image_based", "zzz_bogus"]

    def test_random_pairs_content_class_precedence(self):
        """For 60 random pairs, on the cat_c-boundary tree (leaf_concentration
        0.20, between 0.17 and 0.204):

        - non-empty content_class: verdict is invariant to inspector_class
          (identical to the inspector_class=None result -- precedence holds);
        - empty content_class: cat_c promotion fires iff
          inspector_class == 'text_based'.
        """
        rng = random.Random(0xD1)
        structure = _flat_leaf_tree([20] * 5)
        sig = TreeSignals.from_tree(structure, garble_threshold=0.15)
        gate = TreeGateResult(
            ok=False,
            defect=TreeDefect.DEPTH_LOW,
            detail="depth=1",
            signals=sig,
            all_defects=frozenset({TreeDefect.DEPTH_LOW}),
        )
        for _ in range(_TRIALS):
            content_class = rng.choice(["", *self._CONTENT_CLASSES])
            inspector_class = rng.choice(self._INSPECTOR_CLASSES)
            result = classify_verdict(
                structure, content_class, gate, inspector_class=inspector_class
            )
            if content_class:
                baseline = classify_verdict(structure, content_class, gate)
                assert result == baseline, (
                    f"inspector_class={inspector_class!r} changed the verdict "
                    f"for content_class={content_class!r}: {result} != {baseline}"
                )
                assert result[1] != "cat_c_promoted"
            elif inspector_class == "text_based":
                assert result[0] == "PASS", (content_class, inspector_class, result)
                assert result[1] in ("", "cat_c_promoted"), (
                    content_class,
                    inspector_class,
                    result,
                )
            else:
                assert result == ("MARGINAL", "depth=1"), (content_class, inspector_class, result)


# ---------------------------------------------------------------------------
# D2: landscape orientation detection + rasterize-rotate-reextract fallback
# ---------------------------------------------------------------------------


def _make_pdf(tmp_path, name, width, height, rotate=0):
    doc = fitz.open()
    page = doc.new_page(width=width, height=height)
    if rotate:
        page.set_rotation(rotate)
    path = str(tmp_path / name)
    doc.save(path)
    doc.close()
    return path


class TestOrientationProbe:
    """_tag_landscape_pages_for_fallback correctly tags landscape pages
    (rotated or wide-aspect) and leaves portrait pages untagged."""

    def test_rotated_and_portrait_pages_are_tagged_correctly(self, tmp_path):
        rotated_path = _make_pdf(tmp_path, "rot90.pdf", width=600, height=800, rotate=90)
        rotated_pages = _tag_landscape_pages_for_fallback(rotated_path)
        assert len(rotated_pages) == 1
        assert rotated_pages[0]["is_landscape"] is True
        assert rotated_pages[0]["rotate"] == 90

        portrait_path = _make_pdf(tmp_path, "portrait.pdf", width=600, height=800, rotate=0)
        portrait_pages = _tag_landscape_pages_for_fallback(portrait_path)
        assert len(portrait_pages) == 1
        assert portrait_pages[0]["is_landscape"] is False
        assert portrait_pages[0]["rotate"] == 0


class TestFallbackTriggerSkip:
    """_landscape_pages_below_threshold only flags pages that are BOTH
    landscape-tagged AND below LANDSCAPE_CHAR_THRESHOLD, AND (RFC-036 D0c)
    carry a detectable picture/graphic region."""

    @staticmethod
    def _mock_document(char_count):
        item = SimpleNamespace(text="x" * char_count)
        doc = MagicMock()
        doc.iterate_items.return_value = [(item, 0)]
        return doc

    def test_landscape_page_below_threshold_is_flagged(self, monkeypatch):
        # RFC-036 D0c: a below-threshold landscape page is only flagged when
        # it also carries a detectable picture/graphic region (page 1,
        # 1-indexed) -- otherwise dense numeric-table pages false-positive.
        monkeypatch.setattr(
            converters.pictures, "_collect_picture_regions", lambda doc: [{"page": 1, "bbox": {}}]
        )
        landscape_pages = [{"page_no": 0, "rotate": 0, "is_landscape": True}]
        document = self._mock_document(200)
        below = _landscape_pages_below_threshold(document, landscape_pages)
        assert len(below) == 1
        assert below[0]["page_no"] == 0
        assert below[0]["char_count"] == 200

    def test_landscape_page_below_threshold_without_picture_is_not_flagged(self, monkeypatch):
        # RFC-036 D0c: dense numeric-table pages fall below the char
        # threshold but carry no picture region, so they no longer
        # false-positive trigger the rasterize-rotate-reextract fallback.
        monkeypatch.setattr(converters.pictures, "_collect_picture_regions", lambda doc: [])
        landscape_pages = [{"page_no": 0, "rotate": 0, "is_landscape": True}]
        document = self._mock_document(200)
        below = _landscape_pages_below_threshold(document, landscape_pages)
        assert below == []

    def test_above_threshold_and_portrait_pages_are_not_flagged(self, monkeypatch):
        """Combines two negative-outcome scenarios that share the same
        picture-region mock: a landscape page above the char threshold, and
        a portrait page below it (e.g. a legitimately sparse cover/divider
        page) -- neither must be flagged."""
        monkeypatch.setattr(
            converters.pictures, "_collect_picture_regions", lambda doc: [{"page": 1, "bbox": {}}]
        )

        landscape_above = [{"page_no": 0, "rotate": 0, "is_landscape": True}]
        document_above = self._mock_document(2000)
        assert _landscape_pages_below_threshold(document_above, landscape_above) == []

        portrait_below = [{"page_no": 0, "rotate": 0, "is_landscape": False}]
        document_below = self._mock_document(200)
        assert _landscape_pages_below_threshold(document_below, portrait_below) == []


class TestRasterizationFailureFallthrough:
    """Rasterization failure logs a warning and falls through rather than
    raising."""

    def test_rasterize_failure_falls_through_without_raising(self, tmp_path, monkeypatch, caplog):
        path = _make_pdf(tmp_path, "any.pdf", width=800, height=600, rotate=0)
        monkeypatch.setattr(
            converters.pictures,
            "_rasterize_rotate_page",
            MagicMock(side_effect=RuntimeError("render failed")),
        )
        pages = [{"page_no": 0, "rotate": 0, "is_landscape": True, "char_count": 100}]

        with caplog.at_level("WARNING"):
            result = _landscape_rasterize_rotate_reextract(path, pages)

        assert result == []
        assert any("landscape rasterize/rotate failed" in r.message for r in caplog.records)


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
        "vlm_describe_images": False,
        # HR3: pii_corpus=True + non-ZDR endpoint closes zdr_egress_gate so
        # _generate_flat_doc_description never attempts a real litellm call.
        "pii_corpus": True,
    }
    base.update(overrides)
    return SimpleNamespace(**base)


@pytest.fixture
def pdf_file(tmp_path):
    path = tmp_path / "doc.pdf"
    path.write_bytes(b"%PDF-1.4\n fake pdf bytes")
    return str(path)


def _wire_index(monkeypatch, *, pic_results, flat_return):
    fake_settings = _fake_settings()
    monkeypatch.setattr(_idx, "settings", fake_settings)
    monkeypatch.setattr(_rec, "settings", fake_settings)
    # zdr_egress_gate re-imports settings from .config fresh on every call.
    monkeypatch.setattr("pageindex_mcp.config.settings", fake_settings)
    # pipeline_config is now the canonical source (indexer.py reads
    # pipeline_config.pdf_inspector_preclassify live), so patch the config
    # object rather than a frozen module-level constant.
    monkeypatch.setattr(_idx, "pipeline_config", replace(_idx.pipeline_config, pdf_inspector_preclassify=False))
    monkeypatch.setattr(_idx, "hash_cache_get", lambda filename: None)
    monkeypatch.setattr(_idx, "list_processed_docs", lambda: [])
    monkeypatch.setattr(_idx, "hash_cache_set", MagicMock())
    monkeypatch.setattr(_idx, "validate_tree", lambda structure, **kw: (True, None))
    monkeypatch.setattr(_rec, "validate_tree", lambda structure, **kw: (True, None))
    monkeypatch.setattr(_idx, "prepare_tree", lambda structure, **kw: structure)

    # Large body so the RFC-029 D1 flat-prefer check (which also compares
    # flat vs. tree char counts) does not itself trigger and confound the
    # D2 reroute-on-PictureResults assertion below.
    md_text = "# Heading\n\n" + ("Body paragraph text. " * 300)
    # D3B's flat-path garble gate is orthogonal to the D2 routing decision
    # under test here -- stub it out so repeated filler text in md_text
    # doesn't spuriously trip it.
    _not_garbled = GarbleReport(is_garbled=False, fired_prongs=frozenset())
    monkeypatch.setattr(_idx, "detect_garble", lambda *a, **kw: _not_garbled)
    monkeypatch.setattr(_rec, "detect_garble", lambda *a, **kw: _not_garbled)
    conv_fn = MagicMock(return_value=(md_text, pic_results))
    monkeypatch.setattr(_idx, "pdf_markdown_converters", lambda: [("docling", conv_fn, True)])
    monkeypatch.setattr(_rec, "pdf_to_markdown_docling", MagicMock(return_value=(md_text, [])))
    monkeypatch.setattr(_idx, "ensure_tessdata", lambda langs: langs)
    monkeypatch.setattr(_rec, "ensure_tessdata", lambda langs: langs)

    mocks = {
        "save_doc": MagicMock(),
        "save_flat_doc": MagicMock(),
        "save_raw": MagicMock(),
        "save_doc_meta": MagicMock(),
    }
    for name, m in mocks.items():
        monkeypatch.setattr(_idx, name, m)

    route_and_extract_flat = MagicMock(return_value=flat_return)
    monkeypatch.setattr(_img, "route_and_extract_flat", route_and_extract_flat)
    monkeypatch.setattr(_idx, "route_and_extract_flat", route_and_extract_flat)
    monkeypatch.setattr(_idx, "_garble_check_flat_blocks", lambda *a, **kw: None)
    mocks["route_and_extract_flat"] = route_and_extract_flat

    idx_metrics = {
        "FLAT_DOCS_TOTAL": MagicMock(),
        "LOW_QUALITY_TREES": MagicMock(),
        "VLM_FALLBACK_TOTAL": MagicMock(),
        "RAW_UPLOAD_FAILURES": MagicMock(),
        "PDF_PRIMARY_CONVERTER_FAILURES": MagicMock(),
        "PDF_EXTRACT_FALLBACKS": MagicMock(),
        "PDF_INSPECTOR_FORCED_OCR": MagicMock(),
    }
    for name, m in idx_metrics.items():
        monkeypatch.setattr(_idx, name, m)
    mocks.update(idx_metrics)

    ocr_escalation_total = MagicMock()
    monkeypatch.setattr(_rec, "OCR_ESCALATION_TOTAL", ocr_escalation_total)
    monkeypatch.setattr(_rec, "VLM_FALLBACK_TOTAL", mocks["VLM_FALLBACK_TOTAL"])
    monkeypatch.setattr(_img, "LOW_QUALITY_TREES", mocks["LOW_QUALITY_TREES"])
    mocks["OCR_ESCALATION_TOTAL"] = ocr_escalation_total

    mocks["conv_fn"] = conv_fn
    return mocks


def _make_client():
    return CustomPageIndexClient(api_key="test-key")


async def _run_index(monkeypatch, pdf_file, *, pic_results, flat_return):
    mocks = _wire_index(monkeypatch, pic_results=pic_results, flat_return=flat_return)
    c = _make_client()
    tree_structure = [
        {
            "title": "Section",
            "text": "x",
            "nodes": [{"title": "Leaf", "text": "y " * 4000}],
        }
    ]
    monkeypatch.setattr(
        c,
        "_run_md_to_tree",
        AsyncMock(return_value={"structure": tree_structure, "doc_description": "ok"}),
    )
    doc_id = await c.index(pdf_file)
    return c, doc_id, mocks


class TestRoutingReevaluationAfterFallbackReextraction:
    """A Phase 2 re-extraction that produces PictureResults (tagged
    skipped_reason='landscape_fallback_picture' by the landscape
    rasterize-rotate-reextract fallback) must re-route the document to the
    flat-mixed path instead of letting it stay on the tree path; a
    re-extraction with NO PictureResults must leave the document on its
    original (tree) routing path."""

    async def test_picture_results_reroutes_to_flat_mixed(self, monkeypatch, pdf_file):
        pic_results = [{"page": 1, "skipped_reason": "landscape_fallback_picture"}]
        c, doc_id, mocks = await _run_index(
            monkeypatch,
            pdf_file,
            pic_results=pic_results,
            flat_return=("flat_mixed", [{"role": "prose", "text": "chart caption"}]),
        )

        assert isinstance(doc_id, str)
        mocks["save_flat_doc"].assert_called_once()
        mocks["save_doc"].assert_not_called()
        assert c.last_content_class == "flat_mixed"
        mocks["FLAT_DOCS_TOTAL"].labels.assert_called_once_with(content_class="flat_mixed")

    async def test_no_picture_results_stays_on_original_routing_path(self, monkeypatch, pdf_file):
        c, doc_id, mocks = await _run_index(
            monkeypatch,
            pdf_file,
            pic_results=[],
            flat_return=("flat_prose", [{"role": "prose", "text": "x"}]),
        )

        assert isinstance(doc_id, str)
        mocks["save_doc"].assert_called_once()
        mocks["save_flat_doc"].assert_not_called()
        assert c.last_content_class is None
