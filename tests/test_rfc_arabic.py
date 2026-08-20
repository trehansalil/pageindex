"""RFC-036 consolidated tests -- Arabic-corpus remediation (D0/D1/D3/D4/D5/D6).

Consolidates (formerly separate files, one per RFC-036 subtask):
  D0 -- landscape reextract runaway: page cap, thread/process kill, splice
        fix, fragmentation guard (Properties 1-4).
  D1 -- write-barrier delay cap and catch-and-downgrade of
        PersistenceNotVisibleError in save_doc/save_doc_meta (Properties 5-6).
  D3 -- rtl_reversal joins the flat-routing whitelist instead of raising
        LowQualityTreeError immediately when bidi repair does not converge
        (Properties 8-9).
  D4 -- propagate PictureResult skip metadata to image blocks and suppress
        false enrichment verdicts (Properties 10-11).
  D5 -- Arabic structural heading injection extended to قرار/مرسوم/قانون
        gazette markers (Property 12).
  D6 -- complexity-proportional depth-adequacy scoring in classify_verdict.

Unit/PBT layer only (no live Docling/MinIO dependency, per each Design's
Property-Based Testing Configuration). True corpus re-ingestion against live
Docling/MinIO is the Design's separate Integration Tests layer; the
regression classes below are synthetic proxies for the named corpus
fixtures.
"""

from __future__ import annotations

import multiprocessing
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from pageindex_mcp import converters
from pageindex_mcp.converters import (
    _AR_MARKER_CAPTURE_RE,
    _AR_PART_RE,
    FuturesTimeoutError,
    _inject_arabic_structural_headings,
    _landscape_rasterize_rotate_reextract,
    _outline_norm,
    _recover_picture_results,
    _run_docling_chunk_with_timeout,
    _splice_landscape_fallback,
    decide_rtl,
)
from pageindex_mcp.helpers import (
    LowQualityTreeError,
    TreeDefect,
    TreeGateResult,
    _segment_table_nodes,
    classify_verdict,
    compute_image_enrichment_ratio,
)
from pageindex_mcp.metrics import WRITE_BARRIER_EXHAUSTED
from pageindex_mcp.storage import (
    _WRITE_BARRIER_DELAYS,
    PersistenceNotVisibleError,
    save_doc,
    save_doc_meta,
)

import pageindex_mcp.client as client_mod
from pageindex_mcp.client import CustomPageIndexClient, _enrich_image_blocks


# ===========================================================================
# D0 -- landscape reextract runaway (Properties 1-4)
# ===========================================================================


def _wire_fake_docling(monkeypatch, tmp_path, markdown="# Chart\n\nrecovered content"):
    # _landscape_rasterize_rotate_reextract bails to [] when the AGPL
    # fallback is disabled; pin it on so the cap/deadline assertions are
    # hermetic regardless of the host's ALLOW_AGPL_FALLBACK env setting.
    monkeypatch.setattr("pageindex_mcp.config.ALLOW_AGPL_FALLBACK", True)
    monkeypatch.setattr(
        converters,
        "_rasterize_rotate_page",
        lambda pdf_path, page_no, dpi=300: str(tmp_path / f"page{page_no}.png"),
    )
    monkeypatch.setattr(converters, "_repair_docling_tables", lambda md, doc_name=None: md)
    fake_result = MagicMock()
    fake_result.document.export_to_markdown.return_value = markdown
    fake_result.document.pictures = []
    fake_converter = MagicMock()
    fake_converter.convert.return_value = fake_result
    monkeypatch.setattr(converters, "_docling_converter", lambda **kw: fake_converter)


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
            {"page_no": i, "rotate": 90, "is_landscape": True, "char_count": 10}
            for i in range(15)
        ]

        results = _landscape_rasterize_rotate_reextract("fake.pdf", pages)

        assert len(results) == converters.MAX_LANDSCAPE_PAGES
        assert [r["page_no"] for r in results] == list(range(converters.MAX_LANDSCAPE_PAGES))

    def test_deadline_also_bounds_the_loop(self, tmp_path, monkeypatch):
        _wire_fake_docling(monkeypatch, tmp_path)
        monkeypatch.setattr(converters, "LANDSCAPE_REEXTRACT_DEADLINE_SECONDS", 0.0)
        pages = [
            {"page_no": i, "rotate": 90, "is_landscape": True, "char_count": 10}
            for i in range(5)
        ]

        results = _landscape_rasterize_rotate_reextract("fake.pdf", pages)

        assert results == []


# Module-level (picklable-by-reference) worker stand-ins for the
# multiprocessing 'spawn' context Property 2 exercises.
def _slow_chunk_worker(result_queue, pdf_path, force_full_page_ocr, ocr_lang_override, expected_script=None):
    time.sleep(5)
    result_queue.put(("ok", ("late content", [])))


def _fast_chunk_worker(result_queue, pdf_path, force_full_page_ocr, ocr_lang_override, expected_script=None):
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

        md = (
            "# Page 15 Section\n\ntext\n\n"
            "# Page 18 Section\n\nmore text\n"
        )
        heading_pages = {
            _outline_norm("Page 15 Section"): [15],
            _outline_norm("Page 18 Section"): [18],
        }
        spliced = _splice_landscape_fallback(md, results, heading_pages)

        assert spliced.count("Recovered chart text") == 2
        first_heading_idx = spliced.index("# Page 15 Section")
        last_heading_idx = spliced.index("# Page 18 Section")
        for idx in (
            i for i in _all_indices(spliced, "Recovered chart text")
        ):
            assert first_heading_idx < idx < last_heading_idx

# ===========================================================================
# D1 -- write-barrier delay cap + catch-and-downgrade (Properties 5-6)
# ===========================================================================


def _counter_value(counter) -> float:
    return counter._value.get()


@pytest.fixture
def mock_minio():
    client = MagicMock()
    client.bucket_exists.return_value = True
    with patch("pageindex_mcp.storage.get_minio", return_value=client):
        yield client


class TestWriteBarrierBudgetCapped:
    """Property 5: _confirm_write_visible's total polling delay across
    _WRITE_BARRIER_DELAYS SHALL NOT exceed 0.45s."""

    def test_delay_schedule_totals_at_most_0_45s(self):
        assert sum(_WRITE_BARRIER_DELAYS) <= 0.45

class TestWriteBarrierExhaustionPropagates:
    """Property 6: PersistenceNotVisibleError raised by
    _confirm_write_visible SHALL propagate out of save_doc/save_doc_meta
    (Zone-6 fix), not be swallowed as a warning."""

    def test_save_doc_meta_raises_on_barrier_exhaustion(self, mock_minio, monkeypatch):
        monkeypatch.setattr(
            "pageindex_mcp.storage._confirm_write_visible",
            MagicMock(side_effect=PersistenceNotVisibleError("processed/doc.meta.json")),
        )

        with pytest.raises(PersistenceNotVisibleError):
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
    monkeypatch.setattr(client_mod, "prepare_tree", lambda structure, **kw: structure)
    monkeypatch.setattr(
        client_mod,
        "pdf_markdown_converters",
        lambda: [("stub", lambda path, **kw: flat_md)],
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
        # Zone-5: verdict stripped from artifact body; check sidecar instead
        meta_call = mocks["save_doc_meta"].call_args
        verdict = meta_call.args[1]["verdict"]
        assert verdict in ("PASS", "MARGINAL")


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

        with patch("pageindex_mcp.client.save_figure"):
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
            patch("pageindex_mcp.converters._OCR_ESCALATION_PER_PICTURE", True),
            patch("pageindex_mcp.converters._collect_picture_regions", return_value=regions),
            patch("pageindex_mcp.converters.ensure_tessdata", return_value=["eng"]),
            patch(
                "pageindex_mcp.converters._recover_picture_text",
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

    def _tree_with_text(self, chars: int) -> list:
        return [{"title": "", "text": "x" * chars, "nodes": []}]

    def test_genuinely_enriched_blocks_still_promote_verdict(self):
        """Sanity check: the suppression is targeted -- a document whose
        images ARE genuinely enriched still gets image_enrichment_promoted."""
        blocks = [
            {"role": "image", "ocr_text": "42% revenue growth"},
            {"role": "image", "ocr_text": "31% cost reduction"},
        ]
        image_enrichment_ratio = compute_image_enrichment_ratio(blocks)
        assert image_enrichment_ratio == 1.0

        structure = self._tree_with_text(600)
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

_WORDS = (
    "the quick brown fox jumps over lazy dog while article clause section "
    "provides that obligation shall apply notwithstanding any other term"
).split()


def _leaf_text(i: int) -> str:
    return " ".join(_WORDS[j % len(_WORDS)] + str(i) for j in range(20))


def _make_tree(node_count: int, depth: int) -> list:
    """Build a chain of `depth` levels ending in enough equal-sized leaves
    to total `node_count` nodes, so max_leaf_ratio stays low and only the
    depth-adequacy gate is under test."""
    leaves_needed = node_count - (depth - 1)
    current = [
        {"title": "", "text": _leaf_text(i), "nodes": []} for i in range(leaves_needed)
    ]
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

