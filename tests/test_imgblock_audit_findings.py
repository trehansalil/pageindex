"""Remediation tests for audit/IMAGE_BLOCK_INGESTION_SCALING_AUDIT_2026-07-21.

Couple 1 findings: 1, 2, 3, 4, 6, 7, 8, 10, 11, 12, 14, 15
(converters.py + client.py — the image-block OCR/VLM enrichment pipeline).

Structural contract under test:
- converters return ``(md, pic_results)`` up the chain (no thread-local) — 1/11
- ``[Figure: fig-N]`` splice + VLM run ONLY in the flat branch of client.index() — 6/8
- dense ordinal-keyed pic_results + marker-count==region-count guard — 4/7
- shared zdr_egress_gate whose api_base is passed to litellm.completion — 2/3
- async _enrich_image_blocks: save_figure via to_thread, png released — 14/11
- bounded ThreadPoolExecutor for OCR and VLM — 10
- decorative-image gate — 12
- retry-once + IMAGE_DESCRIBE_FAILURES on VLM failure — 15
"""

import os
import sys
import tempfile
import threading
import types
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

import pageindex_mcp.client as client_mod
from pageindex_mcp import converters
from pageindex_mcp.client import CustomPageIndexClient, _generate_flat_doc_description
from pageindex_mcp.converters import (
    PictureResult,
    _add_vlm_descriptions,
    splice_figure_markers,
    zdr_egress_gate,
)


def _cfg(**over):
    """A minimal stand-in for config.settings covering the fields the image
    pipeline reads (frozen dataclass -> patch the module attribute wholesale)."""
    base = dict(
        pii_corpus=False,
        openai_base_url="https://api.openai.com/v1",
        vlm_model="gpt-4.1",
        vlm_describe_images=False,
        llm_model="gpt-test",
    )
    base.update(over)
    return SimpleNamespace(**base)


def _fake_resp(text):
    return SimpleNamespace(choices=[SimpleNamespace(message=SimpleNamespace(content=text))])


def _install_fake_fitz(monkeypatch):
    class _Pix:
        def tobytes(self, fmt="png"):
            return b"\x89PNG fake image bytes"

        def save(self, path):
            with open(path, "wb") as fh:
                fh.write(b"\x89PNG")

    class _Page:
        rect = types.SimpleNamespace(height=800.0, width=600.0)
        rotation = 0

        def set_rotation(self, value):
            self.rotation = value

        def get_text(self, mode="text", *, clip=None):
            return ""

        def get_pixmap(self, clip, dpi):
            return _Pix()

    class _Pdf:
        page_count = 1

        def __getitem__(self, i):
            return _Page()

        def close(self):
            pass

    fake = types.ModuleType("fitz")
    fake.Rect = lambda *a: types.SimpleNamespace(
        coords=a,
        width=a[2] - a[0] if len(a) >= 4 else 0,
        height=a[3] - a[1] if len(a) >= 4 else 0,
    )
    fake.open = lambda path: _Pdf()
    monkeypatch.setitem(sys.modules, "fitz", fake)


def _region():
    return {"page": 1, "bbox": types.SimpleNamespace(l=0, t=10, r=100, b=110, coord_origin=None)}


# ---------------------------------------------------------------------------
# Findings 2 / 3 — shared HR3 ZDR gate + api_base parity with litellm egress
# ---------------------------------------------------------------------------
class TestFinding2And3ZdrEgressGate:
    def test_gate_blocks_pii_non_zdr(self, monkeypatch):
        monkeypatch.setattr("pageindex_mcp.config.settings", _cfg(pii_corpus=True))
        allowed, api_base = zdr_egress_gate("test purpose", doc_id="d1")
        assert allowed is False
        assert api_base == "https://api.openai.com/v1"

    def test_gate_allows_zdr_endpoint(self, monkeypatch):
        monkeypatch.setattr(
            "pageindex_mcp.config.settings",
            _cfg(pii_corpus=True, openai_base_url="https://myres.openai.azure.com/v1"),
        )
        allowed, api_base = zdr_egress_gate("test purpose")
        assert allowed is True
        assert api_base == "https://myres.openai.azure.com/v1"

    def test_gate_allows_non_pii_corpus(self, monkeypatch):
        monkeypatch.setattr("pageindex_mcp.config.settings", _cfg(pii_corpus=False))
        allowed, _ = zdr_egress_gate("test purpose")
        assert allowed is True

    def test_finding2_flat_desc_gated_pii_non_zdr(self, monkeypatch):
        """HR3: pii_corpus + non-ZDR endpoint => no doc text egresses at all."""
        monkeypatch.setattr("pageindex_mcp.config.settings", _cfg(pii_corpus=True))
        with patch("litellm.completion") as comp:
            out = _generate_flat_doc_description("PII-bearing text", model="gpt-test", doc_id="d1")
        assert out == ""
        comp.assert_not_called()

    def test_finding3_flat_desc_passes_gated_api_base(self, monkeypatch):
        monkeypatch.setattr(
            "pageindex_mcp.config.settings",
            _cfg(openai_base_url="https://eu.api.openai.com/v1"),
        )
        with patch("litellm.completion", return_value=_fake_resp("A doc.")) as comp:
            out = _generate_flat_doc_description("some text", model="gpt-test")
        assert out == "A doc."
        assert comp.call_args.kwargs["api_base"] == "https://eu.api.openai.com/v1"

    def test_finding3_vlm_passes_gated_api_base(self, monkeypatch):
        monkeypatch.setattr(
            "pageindex_mcp.config.settings",
            _cfg(openai_base_url="https://eu.api.openai.com/v1"),
        )
        pics = [PictureResult(png_bytes=b"png", ocr_text="")]
        with patch("litellm.completion", return_value=_fake_resp("a chart")) as comp:
            _add_vlm_descriptions(pics, "doc1")
        assert comp.call_args.kwargs["api_base"] == "https://eu.api.openai.com/v1"
        assert pics[0]["description"] == "a chart"

    def test_hr3_vlm_blocked_no_image_egress(self, monkeypatch):
        monkeypatch.setattr("pageindex_mcp.config.settings", _cfg(pii_corpus=True))
        pics = [PictureResult(png_bytes=b"png")]
        with patch("litellm.completion") as comp:
            _add_vlm_descriptions(pics, "doc1")
        comp.assert_not_called()
        assert "description" not in pics[0]


# ---------------------------------------------------------------------------
# Findings 1 / 6 / 11 — tuple return, no thread-local, neutral tree markdown
# ---------------------------------------------------------------------------
class TestFinding1TupleReturnNoThreadLocal:
    def test_finding1_no_thread_local_symbols(self):
        assert not hasattr(converters, "_picture_results_tls")
        assert not hasattr(converters, "get_last_picture_results")

    def test_finding1_pymupdf_chain_entry_returns_tuple(self, monkeypatch):
        monkeypatch.setattr(converters, "pdf_to_markdown", lambda p: "# md")
        chain = dict(converters.pdf_markdown_converters())
        assert chain["pymupdf4llm"]("dummy.pdf") == ("# md", [], {})

    def test_finding1_finding6_docling_returns_tuple_with_neutral_md(self, monkeypatch):
        md = "# Title\n\n## Section\n\n<!-- image -->\n\nbody text"
        fake_doc = SimpleNamespace(export_to_markdown=lambda: md)
        fake_result = SimpleNamespace(document=fake_doc)
        monkeypatch.setattr(
            converters,
            "_docling_converter",
            lambda **kw: SimpleNamespace(convert=lambda p: fake_result),
        )
        monkeypatch.setattr(converters, "_collect_heading_pages", lambda d: {})
        monkeypatch.setattr(converters, "_repromote_numbered_headings", lambda d: 0)
        pr = PictureResult(ocr_text="Revenue 2024 up 42 percent", png_bytes=b"p", page=1, bbox={})
        monkeypatch.setattr(
            converters, "_recover_picture_results", lambda md_, doc, path, filename=None, body_for_containment=None, expected_script=None: [pr]
        )

        out_md, pics, _stages = converters.pdf_to_markdown_docling("dummy.pdf")

        assert pics == [pr]
        # Finding 6: the converter output (shared by the TREE route) stays neutral.
        assert "<!-- image -->" in out_md
        assert "[Figure:" not in out_md
        assert "[Chart text]" not in out_md


# ---------------------------------------------------------------------------
# Findings 4 / 7 — dense ordinal keying + marker/region count guard
# ---------------------------------------------------------------------------
class TestFinding4And7DenseKeyingAndCountGuard:
    def test_finding4_sparse_regions_keep_index_alignment(self, monkeypatch):
        monkeypatch.setattr(converters, "_OCR_ESCALATION_PER_PICTURE", True)
        monkeypatch.setattr(
            converters, "_collect_picture_regions", lambda d: [_region(), _region(), _region()]
        )
        monkeypatch.setattr(converters, "detect_ocr_langs", lambda s: ["eng"])
        monkeypatch.setattr(converters, "ensure_tessdata", lambda langs: langs)
        pr0 = PictureResult(ocr_text="first chart text here", png_bytes=b"a", page=1, bbox={})
        pr2 = PictureResult(ocr_text="third chart text here", png_bytes=b"c", page=1, bbox={})
        # Region 1's crop failed -> sparse dict; the dense list must NOT collapse.
        # skip_reasons records region 1 as "page_coverage" (F5 plumbing).
        monkeypatch.setattr(
            converters,
            "_recover_picture_text",
            lambda *a, **k: ({0: pr0, 2: pr2}, {1: "page_coverage"}),
        )

        pics = converters._recover_picture_results("x <!-- image --> y", object(), "d.pdf")

        assert len(pics) == 3
        assert pics[0] is pr0
        assert pics[2] is pr2
        assert pics[1].get("skipped_reason") == "page_coverage"
        assert not pics[1].get("ocr_text")
        assert not pics[1].get("png_bytes")

        md = "<!-- image -->\n\n<!-- image -->\n\n<!-- image -->"
        out = splice_figure_markers(md, pics)
        assert "[Figure: fig-0]" in out
        assert "third chart text here" in out
        assert "[Figure: fig-2]" in out
        assert "[Figure: fig-1]" not in out
        assert "<!-- image -->" not in out  # skipped marker stripped by D3

    def test_finding7_count_mismatch_splices_matched_strips_excess(self):
        # RFC-023 D1: count mismatch no longer bails out — the matched marker
        # splices by ordinal; the excess marker (no PictureResult) is stripped.
        md = "<!-- image -->\n\n<!-- image -->"
        pics = [PictureResult(ocr_text="only one result here", png_bytes=b"a")]
        out = splice_figure_markers(md, pics)
        assert "[Figure: fig-0]" in out
        assert "only one result here" in out
        assert "[Figure: fig-1]" not in out
        assert "<!-- image -->" not in out

    def test_empty_pics_is_noop(self):
        assert splice_figure_markers("<!-- image -->", []) == "<!-- image -->"


# ---------------------------------------------------------------------------
# Finding 8 — spliced description is parseable / retrievable downstream
# ---------------------------------------------------------------------------
class TestFinding8DescriptionRetrievable:
    def test_vlm_desc_persists_in_flat_block(self):
        from pageindex_mcp.helpers import route_and_extract_flat

        pics = [
            PictureResult(
                ocr_text="Jan 100 Feb 200 totals",
                png_bytes=b"p",
                description="A pie chart of revenue",
            )
        ]
        md = splice_figure_markers("Intro\n\n<!-- image -->\n\nOutro", pics)
        assert "| A pie chart of revenue]" in md

        _, blocks = route_and_extract_flat(md)
        img = next(b for b in blocks if b.get("role") == "image")
        assert img["index"] == 0
        assert img["description"] == "A pie chart of revenue"
        assert img["ocr_text"] == "Jan 100 Feb 200 totals"


# ---------------------------------------------------------------------------
# Finding 10 — bounded concurrency for OCR and VLM
# ---------------------------------------------------------------------------
class TestFinding10BoundedConcurrency:
    def _recorder(self, monkeypatch, captured):
        real = converters.ThreadPoolExecutor

        class Recorder(real):
            def __init__(self, max_workers=None, **kw):
                captured.append(max_workers)
                super().__init__(max_workers=max_workers, **kw)

        monkeypatch.setattr(converters, "ThreadPoolExecutor", Recorder)

    def test_finding10_vlm_calls_bounded_pool(self, monkeypatch):
        monkeypatch.setattr("pageindex_mcp.config.settings", _cfg())
        captured: list = []
        self._recorder(monkeypatch, captured)
        pics = [PictureResult(png_bytes=b"p") for _ in range(10)]
        with patch("litellm.completion", return_value=_fake_resp("d")):
            _add_vlm_descriptions(pics, "doc1")
        assert captured, "VLM description must run through a ThreadPoolExecutor"
        assert all(w is not None and w <= converters._IMAGE_ENRICH_CONCURRENCY for w in captured)
        assert all("description" in pr for pr in pics)

    def test_finding10_ocr_parallelized_bounded(self, monkeypatch):
        monkeypatch.setattr("pageindex_mcp.config.settings", _cfg())
        _install_fake_fitz(monkeypatch)
        captured: list = []
        self._recorder(monkeypatch, captured)
        monkeypatch.setattr(
            "pageindex_mcp.converters._tesseract_ocr_image",
            lambda png, langs: "Recovered chart text long enough to keep",
        )
        regions = [_region() for _ in range(6)]
        out, _skip = converters._recover_picture_text("dummy.pdf", regions, ["eng"])
        assert len(out) == 6
        assert captured, "per-picture OCR must run through a ThreadPoolExecutor"
        assert all(w is not None and w <= converters._IMAGE_ENRICH_CONCURRENCY for w in captured)


# ---------------------------------------------------------------------------
# Findings 11 / 14 — async enrich: to_thread persistence + png release
# ---------------------------------------------------------------------------
class TestFinding11And14AsyncEnrich:
    async def test_finding14_save_figure_off_event_loop_and_finding11_png_released(self):
        from pageindex_mcp.client import _enrich_image_blocks

        loop_ident = threading.get_ident()
        seen = {}

        def fake_save(doc_id, idx, png):
            seen["thread"] = threading.get_ident()
            seen["args"] = (doc_id, idx, png)
            return f"figures/{doc_id}/fig-{idx}.png"

        pr = {"png_bytes": b"PNGDATA", "page": 3, "bbox": {"l": 1}, "ocr_text": "chart"}
        blocks = [{"role": "image", "index": 0}]
        with patch("pageindex_mcp.client.save_figure", side_effect=fake_save):
            await _enrich_image_blocks(blocks, [pr], "docX")

        assert seen["args"] == ("docX", 0, b"PNGDATA")
        # Finding 14: the blocking MinIO put ran OFF the event-loop thread.
        assert seen["thread"] != loop_ident
        # Finding 11: crop bytes released once persisted.
        assert "png_bytes" not in pr
        assert blocks[0]["figure_path"] == "figures/docX/fig-0.png"
        assert blocks[0]["page"] == 3
        assert blocks[0]["ocr_text"] == "chart"


# ---------------------------------------------------------------------------
# Finding 12 — decorative-image gate
# ---------------------------------------------------------------------------
class TestFinding12DecorativeGate:
    def test_short_ocr_vlm_off_drops_png(self, monkeypatch):
        monkeypatch.setattr("pageindex_mcp.config.settings", _cfg(vlm_describe_images=False))
        _install_fake_fitz(monkeypatch)
        monkeypatch.setattr(
            "pageindex_mcp.converters._tesseract_ocr_image", lambda png, langs: "short"
        )
        out, _skip = converters._recover_picture_text("dummy.pdf", [_region()], ["eng"])
        assert out[0]["ocr_text"] == ""
        assert "png_bytes" not in out[0]

    def test_short_ocr_vlm_on_keeps_png_for_reclassification(self, monkeypatch):
        monkeypatch.setattr("pageindex_mcp.config.settings", _cfg(vlm_describe_images=True))
        _install_fake_fitz(monkeypatch)
        monkeypatch.setattr(
            "pageindex_mcp.converters._tesseract_ocr_image", lambda png, langs: "short"
        )
        out, _skip = converters._recover_picture_text("dummy.pdf", [_region()], ["eng"])
        assert out[0]["ocr_text"] == ""
        assert out[0]["png_bytes"]

    def test_decorative_marker_stays_neutral_in_splice(self):
        pics = [PictureResult(ocr_text="", page=1, bbox={})]
        md = "<!-- image -->"
        assert splice_figure_markers(md, pics) == md


# ---------------------------------------------------------------------------
# Finding 15 — VLM failure: retry once, then metric (no silent-only logging)
# ---------------------------------------------------------------------------
class TestFinding15RetryAndMetric:
    def test_transient_failure_retried_then_succeeds(self, monkeypatch):
        monkeypatch.setattr("pageindex_mcp.config.settings", _cfg())
        monkeypatch.setattr(converters.time, "sleep", lambda s: None)
        calls = {"n": 0}

        def flaky(**kw):
            calls["n"] += 1
            if calls["n"] == 1:
                raise RuntimeError("rate limited")
            return _fake_resp("desc after retry")

        pics = [PictureResult(png_bytes=b"p")]
        with (
            patch("litellm.completion", side_effect=flaky),
            patch("pageindex_mcp.metrics.IMAGE_DESCRIBE_FAILURES") as metric,
        ):
            _add_vlm_descriptions(pics, "d1")

        assert calls["n"] == 2
        assert pics[0]["description"] == "desc after retry"
        metric.labels.assert_not_called()

    def test_persistent_failure_increments_metric(self, monkeypatch):
        monkeypatch.setattr("pageindex_mcp.config.settings", _cfg())
        monkeypatch.setattr(converters.time, "sleep", lambda s: None)
        pics = [PictureResult(png_bytes=b"p")]
        with (
            patch("litellm.completion", side_effect=RuntimeError("boom")),
            patch("pageindex_mcp.metrics.IMAGE_DESCRIBE_FAILURES") as metric,
        ):
            _add_vlm_descriptions(pics, "d1")

        metric.labels.assert_called_once_with(error_type="RuntimeError")
        metric.labels.return_value.inc.assert_called_once()
        assert "description" not in pics[0]


# ---------------------------------------------------------------------------
# Finding 1 (client side) / 8 — flat branch wiring end-to-end-ish
# ---------------------------------------------------------------------------
def _fake_client_settings(vlm_describe_images=False):
    return SimpleNamespace(
        openai_api_key="test-key",
        openai_base_url="https://api.openai.com/v1",
        azure_api_version=None,
        llm_model="gpt-test",
        minio_secure=False,
        minio_endpoint="localhost:9000",
        minio_bucket="pageindex",
        flat_doc_routing=True,
        vlm_fallback=False,
        vlm_model="gpt-4.1",
        vlm_describe_images=vlm_describe_images,
        pii_corpus=False,
    )


@pytest.fixture
def pdf_file():
    fd, path = tempfile.mkstemp(suffix=".pdf")
    with os.fdopen(fd, "wb") as fh:
        fh.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\n fake pdf bytes")
    yield path
    if os.path.exists(path):
        os.unlink(path)


async def _tree_coro():
    return {"structure": [], "doc_description": ""}


def _wire_flat_branch(monkeypatch, *, chain_md, pics, vlm_describe_images=False):
    monkeypatch.setattr(
        client_mod, "settings", _fake_client_settings(vlm_describe_images=vlm_describe_images)
    )
    monkeypatch.setattr(client_mod, "hash_cache_get", lambda filename: None)
    monkeypatch.setattr(client_mod, "list_processed_docs", lambda: [])
    monkeypatch.setattr(client_mod, "hash_cache_set", MagicMock())
    monkeypatch.setattr(client_mod, "validate_tree", lambda structure, **kw: (False, "depth<2"))
    monkeypatch.setattr(client_mod, "split_oversized_leaf_nodes", lambda structure: structure)
    monkeypatch.setattr(client_mod, "_OCR_ESCALATION", False)
    monkeypatch.setattr(client_mod, "_OCR_ESCALATION_GARBLE", False)
    monkeypatch.setattr(
        client_mod, "pdf_markdown_converters", lambda: [("docling", lambda p: (chain_md, pics))]
    )
    monkeypatch.setattr(
        client_mod, "_generate_flat_doc_description", MagicMock(return_value="a flat doc")
    )
    mocks = {
        "save_flat_doc": MagicMock(),
        "save_doc": MagicMock(),
        "save_raw": MagicMock(),
        "save_doc_meta": MagicMock(),
        "save_figure": MagicMock(return_value="figures/x/fig-0.png"),
        "route_and_extract_flat": MagicMock(
            return_value=("flat_prose", [{"role": "image", "index": 0}])
        ),
        "FLAT_DOCS_TOTAL": MagicMock(),
        "LOW_QUALITY_TREES": MagicMock(),
    }
    for name, m in mocks.items():
        monkeypatch.setattr(client_mod, name, m)
    return mocks


CHAIN_MD = "prose line one\n\nprose line two\n\n<!-- image -->\n\nprose line three"


async def test_finding1_flat_enrich_receives_results(monkeypatch, pdf_file):
    """The converter's pic_results reach the flat branch through the RETURN VALUE
    (not a thread-local), get spliced into the flat markdown, and drive
    _enrich_image_blocks -> save_figure with the real doc_id."""
    pr = {"png_bytes": b"PNG", "page": 1, "bbox": {"l": 1}, "ocr_text": "long chart text here"}
    mocks = _wire_flat_branch(monkeypatch, chain_md=CHAIN_MD, pics=[pr])
    c = CustomPageIndexClient(api_key="test-key")
    monkeypatch.setattr(c, "_run_md_to_tree", lambda *a, **k: _tree_coro())

    doc_id = await c.index(pdf_file)

    # Splice happened in the flat branch: the routed markdown carries the marker.
    routed_md = mocks["route_and_extract_flat"].call_args.args[0]
    assert "[Figure: fig-0]" in routed_md
    assert "long chart text here" in routed_md
    # Enrichment persisted the PNG under the real doc_id (HR2 prefix).
    mocks["save_figure"].assert_called_once_with(doc_id, 0, b"PNG")
    # The persisted flat blocks carry the figure metadata. The spliced pic
    # has _spliced_into_markdown flag set (non-destructive splice).
    saved_blocks = mocks["save_flat_doc"].call_args.args[1]["blocks"]
    img = next(b for b in saved_blocks if b.get("role") == "image")
    assert img["figure_path"] == "figures/x/fig-0.png"


async def test_finding8_vlm_runs_in_flat_branch_when_enabled(monkeypatch, pdf_file):
    """VLM descriptions run in the flat branch (the only consumer) with the real
    doc_id — never orphaned converter-side spend."""
    pr = {"png_bytes": b"PNG", "page": 1, "bbox": {}, "ocr_text": "long chart text here"}
    _wire_flat_branch(monkeypatch, chain_md=CHAIN_MD, pics=[pr], vlm_describe_images=True)
    vlm_mock = MagicMock()
    monkeypatch.setattr(client_mod, "_add_vlm_descriptions", vlm_mock)
    c = CustomPageIndexClient(api_key="test-key")
    monkeypatch.setattr(c, "_run_md_to_tree", lambda *a, **k: _tree_coro())

    doc_id = await c.index(pdf_file)

    vlm_mock.assert_called_once()
    args = vlm_mock.call_args.args
    assert args[0] == [pr]
    assert args[1] == doc_id


async def test_vlm_off_by_default_not_called_in_flat_branch(monkeypatch, pdf_file):
    """RFC-004 user-locked: vlm_describe_images defaults false -> no VLM call."""
    pr = {"png_bytes": b"PNG", "page": 1, "bbox": {}, "ocr_text": "long chart text here"}
    _wire_flat_branch(monkeypatch, chain_md=CHAIN_MD, pics=[pr], vlm_describe_images=False)
    vlm_mock = MagicMock()
    monkeypatch.setattr(client_mod, "_add_vlm_descriptions", vlm_mock)
    c = CustomPageIndexClient(api_key="test-key")
    monkeypatch.setattr(c, "_run_md_to_tree", lambda *a, **k: _tree_coro())

    await c.index(pdf_file)

    vlm_mock.assert_not_called()
