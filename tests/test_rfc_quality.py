"""RFC-021 quality-gate tests, consolidated and trimmed."""

import os
import tempfile
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from pageindex_mcp.client import CustomPageIndexClient
from pageindex_mcp.client import images as _img
from pageindex_mcp.client import indexer as _idx
from pageindex_mcp.client import recovery as _rec
from pageindex_mcp.config import reset_pipeline_config
from pageindex_mcp.helpers import (
    BULK_PROFILE,
    _classify_image_verdict,
    _flatten_tree_text,
    _garble_ratio,
    _is_morphologically_nonsense,
    classify_verdict,
    garble_prongs,
    validate_tree,
)
from tests.conftest import filler_text

from tests._garble_compat import check_garble


def _fake_settings(flat_doc_routing: bool = True):
    return SimpleNamespace(
        openai_api_key="test-key",
        openai_base_url="https://api.openai.com/v1",
        azure_api_version=None,
        llm_model="gpt-test",
        minio_secure=False,
        minio_endpoint="localhost:9000",
        minio_bucket="pageindex",
        flat_doc_routing=flat_doc_routing,
        vlm_fallback=False,
        vlm_model="gpt-4.1",
        vlm_describe_images=False,
        pii_corpus=False,
    )


def _make_client():
    return CustomPageIndexClient(api_key="test-key")


@pytest.fixture
def pdf_file_with_content():
    fd, path = tempfile.mkstemp(suffix=".pdf")
    with os.fdopen(fd, "wb") as fh:
        fh.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n real-looking pdf bytes")
    yield path
    if os.path.exists(path):
        os.unlink(path)


async def _tree_coro():
    return {"structure": [{"node_id": "n1", "text": "x", "nodes": []}], "doc_description": ""}


def _tree_result():
    return _tree_coro()


_NUMERIC_JUNK = "1651001429" * 60


def _wire_garble_probe(
    monkeypatch, *, page_text, validate_return=(True, None), conv_return="# converted md"
):
    monkeypatch.setattr(_idx, "settings", _fake_settings(flat_doc_routing=True))
    monkeypatch.setattr(_idx, "hash_cache_get", lambda filename: None)
    monkeypatch.setattr(_idx, "list_processed_docs", lambda: [])
    monkeypatch.setattr(_idx, "hash_cache_set", MagicMock())
    monkeypatch.setattr(_idx, "validate_tree", lambda structure, **kw: validate_return)
    monkeypatch.setattr(_idx, "prepare_tree", lambda structure, **kw: structure)

    mock_page = MagicMock()
    mock_page.get_text.return_value = page_text
    mock_doc = MagicMock()
    mock_doc.page_count = 1
    mock_doc.__enter__ = MagicMock(return_value=mock_doc)
    mock_doc.__exit__ = MagicMock(return_value=False)
    mock_doc.__getitem__ = MagicMock(return_value=mock_page)
    monkeypatch.setattr("fitz.open", MagicMock(return_value=mock_doc))

    conv_mock = MagicMock(return_value=conv_return)
    monkeypatch.setattr(_idx, "pdf_markdown_converters", lambda: [("docling", conv_mock, True)])

    mocks = {
        "save_doc": MagicMock(),
        "save_flat_doc": MagicMock(),
        "save_raw": MagicMock(),
        "save_doc_meta": MagicMock(),
        "route_and_extract_flat": MagicMock(
            return_value=("flat_prose", [{"role": "prose", "text": "x"}])
        ),
        "FLAT_DOCS_TOTAL": MagicMock(),
        "LOW_QUALITY_TREES": MagicMock(),
        "OCR_ESCALATION_TOTAL": MagicMock(),
        "splice_picture_text_for_tree": MagicMock(side_effect=lambda md, pics: md),
    }
    for name, m in mocks.items():
        if name in ("route_and_extract_flat",) or name in ("OCR_ESCALATION_TOTAL",):
            monkeypatch.setattr(_rec, name, m)
        else:
            monkeypatch.setattr(_idx, name, m)
    return mocks, conv_mock


class TestOcrDeferralQF1:
    async def test_ocr_deferral_default(self, monkeypatch, pdf_file_with_content):
        monkeypatch.delenv("PRE_GARBLE_FORCE_OCR_ENABLED", raising=False)
        mocks, conv_mock = _wire_garble_probe(monkeypatch, page_text=_NUMERIC_JUNK)
        c = _make_client()
        monkeypatch.setattr(c, "_run_md_to_tree", lambda *a, **k: _tree_result())
        await c.index(pdf_file_with_content)
        conv_mock.assert_called_once_with(pdf_file_with_content, expected_script="Latn")
        mocks["save_doc"].assert_called_once()

    async def test_fix3_retry_still_fires(self, monkeypatch, pdf_file_with_content):
        monkeypatch.delenv("PRE_GARBLE_FORCE_OCR_ENABLED", raising=False)
        mocks, conv_mock = _wire_garble_probe(monkeypatch, page_text=_NUMERIC_JUNK)
        vt = MagicMock(side_effect=[(False, "garbling"), (True, None)])
        monkeypatch.setattr(_idx, "validate_tree", vt)
        monkeypatch.setattr(_rec, "validate_tree", vt)
        ocr_langs = lambda sample: ["eng"]
        tessdata = lambda langs: langs
        monkeypatch.setattr(_idx, "detect_ocr_langs", ocr_langs)
        monkeypatch.setattr(_rec, "detect_ocr_langs", ocr_langs)
        monkeypatch.setattr(_idx, "ensure_tessdata", tessdata)
        monkeypatch.setattr(_rec, "ensure_tessdata", tessdata)
        escalation_calls = []

        def _fake_pdf_to_markdown_docling(path, force_full_page_ocr, langs, **kwargs):
            escalation_calls.append({"force_full_page_ocr": force_full_page_ocr})
            return "# ocr-recovered md"

        monkeypatch.setattr(_rec, "pdf_to_markdown_docling", _fake_pdf_to_markdown_docling)
        c = _make_client()
        monkeypatch.setattr(c, "_run_md_to_tree", lambda *a, **k: _tree_result())
        await c.index(pdf_file_with_content)
        assert len(escalation_calls) == 1
        assert escalation_calls[0]["force_full_page_ocr"] is True


def _shared_root_tree(leaf_sizes, corrupt_first=False):
    leaves = []
    for i, size in enumerate(leaf_sizes):
        text = filler_text(size, i)
        if corrupt_first and i == 0:
            text = text[:-1] + "\x00"
        leaves.append({"title": "", "text": text, "nodes": []})
    return [{"title": "", "text": "", "nodes": leaves}]


def _make_tree(leaf_sizes, depth=2):
    trees = []
    for idx, size in enumerate(leaf_sizes):
        leaf = {"title": "", "text": filler_text(size, idx), "nodes": []}
        node = leaf
        for _ in range(depth - 1):
            node = {"title": "", "text": "", "nodes": [node]}
        trees.append(node)
    return trees


def _diverse_words(n):
    return " ".join(f"word{i}" for i in range(n))


def _two_leaf_flat_tree():
    return _shared_root_tree([500, 500])


class TestImageEnrichmentPromotionQF2a:
    def test_high_enrichment_passes(self):
        tree = _two_leaf_flat_tree()
        verdict, reason = classify_verdict(tree, "flat_prose", None, image_enrichment_ratio=1.0)
        assert (verdict, reason) == ("PASS", "image_enrichment_promoted")


class TestPassMaxLeafRatioQF2b:
    def test_ratio_016_passes(self):
        tree = _make_tree([160] + [10] * 84, depth=4)
        verdict, reason = classify_verdict(tree, "default", None)
        assert (verdict, reason) == ("PASS", "")


class TestSmallDocExemptionQF2c:
    def test_small_doc_promoted(self):
        with patch.dict(os.environ, {"PASS_MAX_LEAF_RATIO": "0.10"}):
            reset_pipeline_config()
            tree = _shared_root_tree([216, 164, 164, 164, 164, 164, 164])
            verdict, reason = classify_verdict(tree, "flat_prose", None)
        reset_pipeline_config()
        assert (verdict, reason) == ("PASS", "small_doc_promoted")


class TestGarbleRatioQF4:
    def test_clean_text(self):
        text = "The quick brown fox jumps over the lazy dog. " * 50
        assert _garble_ratio(text) == 0.0

    def test_fully_garbled(self):
        text = "" * 3000
        assert _garble_ratio(text) == 1.0


_NON_EMPTY_STRUCTURE = [{"node_id": "1", "title": "Cover", "text": "img", "nodes": []}]


class TestClassifyImageVerdict:
    def test_pass_full_enrichment(self):
        verdict, reason = _classify_image_verdict(1.0)
        assert verdict == "PASS"
        assert reason == "image_enrichment_complete"

    def test_fail_no_enrichment(self):
        verdict, reason = _classify_image_verdict(None)
        assert (verdict, reason) == ("FAIL", "no_image_enrichment")


class TestImageStandaloneClientRouting:
    async def test_env_enabled_promotes_content_class(self, monkeypatch, pdf_file_with_content):
        from pageindex_mcp.helpers import GarbleReport

        _IMAGE_LIGHT_MD = "\n".join(["<!-- image -->"] * 2 + ["some real text line"] * 5)
        _ALL_IMAGE_BLOCKS = [{"role": "image", "index": 0}, {"role": "image", "index": 1}]
        fake_settings = _fake_settings()
        monkeypatch.setattr(_idx, "settings", fake_settings)
        monkeypatch.setattr(_img, "settings", fake_settings)
        monkeypatch.setattr(_idx, "hash_cache_get", lambda filename: None)
        monkeypatch.setattr(_idx, "list_processed_docs", lambda: [])
        monkeypatch.setattr(_idx, "hash_cache_set", MagicMock())
        monkeypatch.setattr(_idx, "validate_tree", MagicMock(side_effect=[(False, "depth<2")]))
        monkeypatch.setattr(
            _idx,
            "pdf_markdown_converters",
            lambda: [("docling", lambda p, **kw: _IMAGE_LIGHT_MD, True)],
        )
        monkeypatch.setattr(_idx, "prepare_tree", lambda structure, **kw: structure)
        monkeypatch.setattr(
            _idx,
            "detect_garble",
            MagicMock(
                return_value=GarbleReport(is_garbled=False, fired_prongs=frozenset()),
            ),
        )
        route_flat_mock = MagicMock(
            return_value=("flat_prose", [dict(b) for b in _ALL_IMAGE_BLOCKS])
        )
        flat_docs_mock = MagicMock()
        mocks = {
            "save_doc": MagicMock(),
            "save_flat_doc": MagicMock(),
            "save_raw": MagicMock(),
            "save_doc_meta": MagicMock(),
            "FLAT_DOCS_TOTAL": flat_docs_mock,
            "LOW_QUALITY_TREES": MagicMock(),
        }
        for name, m in mocks.items():
            monkeypatch.setattr(_idx, name, m)
        monkeypatch.setattr(_rec, "OCR_ESCALATION_TOTAL", MagicMock())
        monkeypatch.setattr(_img, "route_and_extract_flat", route_flat_mock)
        monkeypatch.setattr(_img, "LOW_QUALITY_TREES", MagicMock())
        monkeypatch.setattr(_img, "_IMAGE_STANDALONE_PIPELINE_ENABLED", True)
        c = _make_client()
        monkeypatch.setattr(c, "_run_md_to_tree", lambda *a, **k: _tree_result())
        await c.index(pdf_file_with_content)
        flat_docs_mock.labels.assert_called_once_with(content_class="image_standalone")


_BILINGUAL_ARABIC_ENGLISH = (
    "هذه اتفاقية مستوى الخدمة Service Level Agreement "
    "تحدد معايير الأداء performance metrics "
    "ومستويات التوفر availability targets "
    "للبنية التحتية infrastructure services "
    "المقدمة بموجب هذا العقد contract "
    "لضمان compliance والامتثال للمعايير الدولية "
    "وتحقيق maintenance standards المطلوبة "
    "بما يشمل bandwidth و latency requirements "
    "وفقا لسياسات provider المعتمدة "
    "مع مراعاة customer obligations "
    "وشروط termination و liability المنصوص عليها "
    "في هذا الاتفاق المبرم بين الطرفين المتعاقدين"
)
_PURE_ARABIC = "بسم الله الرحمن الرحيم " * 20
_LATIN_GIBBERISH = " ".join(["xkjqz vbwm nfrl qpzx wblk"] * 60)


class TestBilingualNotGarbled:
    def test_is_garbled_blob_bilingual_not_flagged(self):
        assert (
            check_garble(_BILINGUAL_ARABIC_ENGLISH, expected_script="Arab", profile=BULK_PROFILE)
            is False
        )

    def test_pure_arabic_unchanged(self):
        assert check_garble(_PURE_ARABIC, expected_script="Arab", profile=BULK_PROFILE) is False


class TestActualGarbledStillDetected:
    def test_null_bytes_detected(self):
        assert (
            check_garble("some text\x00 with nulls", expected_script=None, profile=BULK_PROFILE)
            is True
        )

    def test_pua_chars_detected(self):
        pua_text = "normal " + "" * 20 + " text"
        assert check_garble(pua_text, expected_script=None, profile=BULK_PROFILE) is True


class TestSparseMojibakeRealCorruption:
    def test_arabic_latin_arabic_glued_fragments(self):
        clean = "كلمة " * 10
        fragment = "كلمةXYZكلمة "
        text = clean + fragment * 30
        assert "sparse_mojibake" in garble_prongs(text, original_text=text)


class TestMorphologicalNonsense:
    def test_garbled_tokens_are_nonsense(self):
        garbled = ["xKjQ7", "mZpR3", "vBnL8", "wQxR5", "kLpZ9"]
        for token in garbled:
            assert _is_morphologically_nonsense(token) is True, f"{token} not flagged"


class TestQF3RegressionExistingGarbleCases:
    def test_validate_tree_garble_fails(self):
        structure = [
            {
                "title": "root",
                "text": "root",
                "nodes": [
                    {
                        "title": "child",
                        "text": _LATIN_GIBBERISH,
                        "nodes": [{"title": "grandchild", "text": _LATIN_GIBBERISH, "nodes": []}],
                    },
                    {"title": "child2", "text": _LATIN_GIBBERISH, "nodes": []},
                ],
            }
        ]
        ok, reason = validate_tree(structure, expected_script="Arab")
        assert ok is False
        assert reason == "garbling"

    def test_tree_bulk_garble_expected_script_threading(self):

        nodes = [{"text": _LATIN_GIBBERISH}]
        assert (
            check_garble(_flatten_tree_text(nodes), expected_script="Arab", profile=BULK_PROFILE)
            is True
        )
        nodes = [{"text": _PURE_ARABIC}]
        assert (
            check_garble(_flatten_tree_text(nodes), expected_script="Arab", profile=BULK_PROFILE)
            is False
        )
