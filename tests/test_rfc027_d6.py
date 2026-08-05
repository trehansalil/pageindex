"""Tests for RFC-027 Task 1.3 (D6): deduplicate identical adjacent
`<!-- image -->` markers from Docling standalone-image export.

Validates Design Property 7: `re.sub(r'(<!-- image -->)\\s*(?=<!-- image -->)',
'', md_content)` collapses only markers separated by nothing but whitespace
into a single marker; markers separated by any non-whitespace content are
untouched, preserving the RFC-018 D0 multi-region `PictureResult`
replication design.
"""

import os
import re
import tempfile
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from pageindex_mcp import client as client_mod
from pageindex_mcp.client import CustomPageIndexClient
from pageindex_mcp.converters import splice_figure_markers

_DEDUP_RE = re.compile(r"(<!-- image -->)\s*(?=<!-- image -->)")


def _fake_settings():
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
    pic_results list passed to splice_figure_markers -- mirrors the RFC-018
    D0 harness in test_image_blocks.py."""
    fd, jpg_path = tempfile.mkstemp(suffix=".jpg")
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(source_bytes)

        monkeypatch.setattr(client_mod, "settings", _fake_settings())
        monkeypatch.setattr(client_mod, "hash_cache_get", lambda filename: None)
        monkeypatch.setattr(client_mod, "list_processed_docs", lambda: [])
        monkeypatch.setattr(client_mod, "hash_cache_set", lambda *a, **kw: None)
        monkeypatch.setattr(client_mod, "validate_tree", lambda s, **kw: (False, "depth<2"))
        monkeypatch.setattr(
            client_mod,
            "route_and_extract_flat",
            lambda md: ("flat_prose", [{"role": "prose", "text": "x"}]),
        )
        monkeypatch.setattr(client_mod, "save_flat_doc", lambda *a, **kw: None)
        monkeypatch.setattr(client_mod, "save_doc", lambda *a, **kw: None)
        monkeypatch.setattr(client_mod, "save_raw", lambda *a, **kw: None)
        monkeypatch.setattr(client_mod, "save_doc_meta", lambda *a, **kw: None)
        monkeypatch.setattr(client_mod, "FLAT_DOCS_TOTAL", MagicMock())
        monkeypatch.setattr(client_mod, "LOW_QUALITY_TREES", MagicMock())
        monkeypatch.setattr(client_mod, "find_prior_verdict", lambda *a, **kw: None)
        monkeypatch.setattr(client_mod, "ensure_tessdata", lambda langs: langs)
        monkeypatch.setattr(client_mod, "image_to_markdown", lambda path, langs: markdown)

        captured_pics = []
        orig_splice = splice_figure_markers

        def spy_splice(md, pics):
            captured_pics.extend(pics)
            return orig_splice(md, pics)

        monkeypatch.setattr(client_mod, "splice_figure_markers", spy_splice)

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

    def test_single_space_separated_markers_collapse(self):
        md = "<!-- image --> <!-- image -->"
        assert _DEDUP_RE.sub("", md).count("<!-- image -->") == 1

    def test_three_consecutive_whitespace_gapped_markers_collapse_to_one(self):
        md = "<!-- image -->\n\n<!-- image -->\n\n<!-- image -->"
        assert _DEDUP_RE.sub("", md).count("<!-- image -->") == 1

    def test_markers_separated_by_content_are_preserved(self):
        """Markers separated by any non-whitespace content (e.g. distinct
        figure captions) must NOT collapse -- RFC-018 D0 multi-region design."""
        md = "<!-- image -->\n\nSome caption text\n\n<!-- image -->"
        result = _DEDUP_RE.sub("", md)
        assert result.count("<!-- image -->") == 2
        assert "Some caption text" in result

    def test_markers_separated_by_single_non_whitespace_char_are_preserved(self):
        """Boundary case: a single stray non-whitespace character between
        markers is enough to block the dedup -- the guard is content-based,
        not marker-count-based."""
        md = "<!-- image -->.<!-- image -->"
        result = _DEDUP_RE.sub("", md)
        assert result.count("<!-- image -->") == 2

    def test_mixed_run_only_collapses_whitespace_gapped_pair(self):
        """Two markers whitespace-gapped, followed by a third separated by
        content: the first pair collapses, the third distinct marker
        survives untouched."""
        md = "<!-- image -->\n\n<!-- image -->\n\nDistinct figure\n\n<!-- image -->"
        result = _DEDUP_RE.sub("", md)
        assert result.count("<!-- image -->") == 2
        assert "Distinct figure" in result


class TestMarkerDedupIntegration:
    @pytest.mark.asyncio
    async def test_whitespace_gapped_duplicate_markers_produce_single_picture_result(
        self, monkeypatch
    ):
        """Two whitespace-only-separated `<!-- image -->` markers (Docling's
        duplicate-export defect) must dedup to a single PictureResult."""
        source_bytes = b"\xff\xd8\xff\xe0FAKE_JPEG_DUP"
        markdown = "# Title\n\n<!-- image -->\n\n<!-- image -->"
        pics = await _run_index_with_markdown(monkeypatch, markdown, source_bytes)
        assert len(pics) == 1

    @pytest.mark.asyncio
    async def test_content_separated_markers_still_produce_two_picture_results(self, monkeypatch):
        """Two markers separated by real content (distinct figures on the
        same page) must NOT dedup -- multi-region replication preserved."""
        source_bytes = b"\xff\xd8\xff\xe0FAKE_JPEG_TWO_FIGS"
        markdown = "# Title\n\n<!-- image -->\n\nFigure 1 caption\n\n<!-- image -->"
        pics = await _run_index_with_markdown(monkeypatch, markdown, source_bytes)
        assert len(pics) == 2
