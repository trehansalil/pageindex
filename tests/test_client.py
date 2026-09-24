# tests/test_client.py
"""No-infra unit tests for the client / RAG / documents-tool surface.

Covers the pure helpers that moved out of config.py (no_llm_outside_provider
governance rule): _is_azure_url, get_openai_client, and the _SUPPORTED set.
None of these tests require MinIO, Redis, or network access — constructing an
AsyncOpenAI/AsyncAzureOpenAI client does not perform any I/O.

Also absorbs the former ``test_rag.py`` (RAG contract / dedup / pagination),
``test_documents_tools.py`` (not-found paths + erasure-manifest guard +
delete_doc logging), ``test_heuristic_registry.py`` (RFC-041 D5) and
``test_preprocess_client_supported.py`` (RFC-015 D1), grouped by the
production function each test exercises.

Table-driven tests loop internally and name every offending row, so one
collected test carries the coverage a parametrize table did.
"""

import asyncio
import copy
import json
import logging
import os
import tempfile
from datetime import date, timedelta
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import openai
import pytest
from minio.error import S3Error

from pageindex_mcp.client import (
    _IMAGE_EXTS,
    _SUPPORTED,
    CustomPageIndexClient,
    _is_azure_url,
    configure_litellm,
    get_openai_client,
    resolve_llm_provider,
    validate_llm_config,
)
from pageindex_mcp.client import images as _img
from pageindex_mcp.client import indexer as _idx
from pageindex_mcp.client import recovery as _rec
from pageindex_mcp.helpers import _segment_table_nodes
from pageindex_mcp.helpers.heuristic_registry import (
    _HEURISTIC_EXPIRED_GAUGE,
    _HEURISTIC_FIRE_COUNTER,
    HeuristicEntry,
    HeuristicRegistry,
    registry,
)
from pageindex_mcp.storage.documents import (
    _ERASURE_MANIFEST,
    _KNOWN_STORAGE_PREFIXES,
    _PREFIX_TO_ERASURE_STEPS,
    ErasureStep,
    validate_erasure_manifest,
)
from pageindex_mcp.tools import documents
from pageindex_mcp.tools.documents import find_relevant_documents


def _fake_settings(**overrides):
    """A mutable stand-in for the frozen Settings singleton.

    The real `settings` is a frozen dataclass, so we replace the whole name in
    the client module rather than mutating individual attributes.
    """
    base = {
        "openai_base_url": "https://api.openai.com/v1",
        "openai_api_key": "test-key",
        "azure_api_version": None,
        "llm_provider": "auto",
    }
    base.update(overrides)
    return SimpleNamespace(**base)


def test_is_azure_url_table():
    """_is_azure_url recognises Azure endpoints and rejects everything else,
    including None and the empty string."""
    cases = [
        ("https://my-resource.openai.azure.com/", True),
        ("https://foo.openai.azure.com/v1/chat", True),
        ("https://api.openai.com/v1", False),
        (None, False),
        ("", False),
    ]
    failures = [
        f"  {url!r}: expected {exp}, got {_is_azure_url(url)}"
        for url, exp in cases
        if _is_azure_url(url) is not exp
    ]
    assert not failures, "_is_azure_url regressions:\n" + "\n".join(failures)


def test_get_openai_client_per_provider(monkeypatch):
    """LLM-01-C2 / LLM-01-C3: an Azure base URL yields an AsyncAzureOpenAI
    client; a plain OpenAI URL and an OpenAI-compatible endpoint both yield a
    non-Azure AsyncOpenAI, the latter carrying the custom base_url."""
    monkeypatch.setattr(
        "pageindex_mcp.client.llm.settings",
        _fake_settings(
            openai_base_url="https://my-resource.openai.azure.com",
            azure_api_version="2024-08-01-preview",
        ),
    )
    azure_client = get_openai_client()
    assert isinstance(azure_client, openai.AsyncAzureOpenAI)
    assert isinstance(azure_client, openai.AsyncOpenAI)  # AzureOpenAI subclasses OpenAI

    monkeypatch.setattr(
        "pageindex_mcp.client.llm.settings",
        _fake_settings(openai_base_url="https://api.openai.com/v1"),
    )
    plain = get_openai_client()
    assert isinstance(plain, openai.AsyncOpenAI)
    assert not isinstance(plain, openai.AsyncAzureOpenAI)

    monkeypatch.setattr(
        "pageindex_mcp.client.llm.settings",
        _fake_settings(
            llm_provider="compatible",
            openai_base_url="https://openrouter.ai/api/v1",
            openai_api_key="sk-compat",
        ),
    )
    compat = get_openai_client()
    assert isinstance(compat, openai.AsyncOpenAI)
    assert not isinstance(compat, openai.AsyncAzureOpenAI)
    assert str(compat.base_url).rstrip("/") == "https://openrouter.ai/api/v1"


# ---------------------------------------------------------------------------
# LLM-01: OpenAI-compatible endpoint provider abstraction
# ---------------------------------------------------------------------------


def test_resolve_llm_provider_table(monkeypatch):
    """LLM-01-C1: 'auto' infers azure from an Azure URL and openai otherwise,
    an explicit provider overrides base-URL inference, and an invalid
    LLM_PROVIDER fails fast with ValueError rather than being auto-routed."""
    cases = [
        ("auto", "https://r.openai.azure.com", "azure"),
        ("auto", "https://api.openai.com/v1", "openai"),
        # 'compatible' is honored verbatim even though the base URL is not Azure.
        ("compatible", "https://openrouter.ai/api/v1", "compatible"),
    ]
    failures = []
    for provider, base_url, expected in cases:
        monkeypatch.setattr(
            "pageindex_mcp.client.llm.settings",
            _fake_settings(llm_provider=provider, openai_base_url=base_url),
        )
        got = resolve_llm_provider()
        if got != expected:
            failures.append(f"  [{provider} @ {base_url}] expected={expected}, got={got}")
    assert not failures, "resolve_llm_provider regressions:\n" + "\n".join(failures)

    # A typo must surface as a ValueError at startup rather than silently
    # routing traffic to a base-URL-inferred backend.
    monkeypatch.setattr(
        "pageindex_mcp.client.llm.settings",
        _fake_settings(llm_provider="bogus", openai_base_url="https://api.openai.com/v1"),
    )
    with pytest.raises(ValueError, match="Invalid LLM_PROVIDER"):
        resolve_llm_provider()


def test_configure_litellm_openai_and_azure(monkeypatch):
    """LLM-01-C4: configure_litellm sets litellm.api_base/api_key for the
    openai/compatible providers, and the AZURE_* env vars litellm requires
    for the azure provider."""
    import litellm

    monkeypatch.setattr(
        "pageindex_mcp.client.llm.settings",
        _fake_settings(
            llm_provider="compatible",
            openai_base_url="http://localhost:8000/v1",
            openai_api_key="sk-local",
        ),
    )
    monkeypatch.setattr(litellm, "api_base", None, raising=False)
    monkeypatch.setattr(litellm, "api_key", None, raising=False)
    configure_litellm()
    assert litellm.api_base == "http://localhost:8000/v1"
    assert litellm.api_key == "sk-local"

    monkeypatch.delenv("AZURE_API_BASE", raising=False)
    monkeypatch.delenv("AZURE_API_KEY", raising=False)
    monkeypatch.delenv("AZURE_API_VERSION", raising=False)
    monkeypatch.setattr(litellm, "api_base", None, raising=False)
    monkeypatch.setattr(
        "pageindex_mcp.client.llm.settings",
        _fake_settings(
            llm_provider="azure",
            openai_base_url="https://r.openai.azure.com",
            openai_api_key="sk-azure",
            azure_api_version="2024-08-01-preview",
        ),
    )
    configure_litellm()
    assert os.environ["AZURE_API_BASE"] == "https://r.openai.azure.com"
    assert os.environ["AZURE_API_KEY"] == "sk-azure"
    assert os.environ["AZURE_API_VERSION"] == "2024-08-01-preview"
    assert litellm.api_base == "https://r.openai.azure.com"


def test_validate_llm_config_fails_fast_on_missing_fields(monkeypatch):
    """LLM-01-C5: an empty API key or base URL fails fast, while a
    well-formed compatible config validates without raising."""
    monkeypatch.setattr("pageindex_mcp.client.llm.settings", _fake_settings(openai_api_key=""))
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        validate_llm_config()

    monkeypatch.setattr(
        "pageindex_mcp.client.llm.settings",
        _fake_settings(openai_api_key="sk-x", openai_base_url=""),
    )
    with pytest.raises(ValueError, match="OPENAI_BASE_URL"):
        validate_llm_config()

    monkeypatch.setattr(
        "pageindex_mcp.client.llm.settings",
        _fake_settings(
            llm_provider="compatible",
            openai_api_key="sk-x",
            openai_base_url="https://openrouter.ai/api/v1",
        ),
    )
    validate_llm_config()


# ---------------------------------------------------------------------------
# RFC-033 D6 — table segmentation runs on every tree-build path
# ---------------------------------------------------------------------------
#
# _segment_table_nodes (helpers.py) is a pure, path-agnostic function: client.py
# calls it identically regardless of which tree-build branch (primary,
# garble-recovery, image-escalation) produced the structure being segmented.
# These tests exercise the function directly to validate Design Property 6 —
# segmentation behavior is the same no matter which caller invokes it, and
# structures that already went through segmentation on a garble-recovery path
# are not altered by a second pass (the primary-path call added by D6).


def _pipe_table(n_data_rows: int, n_cols: int = 3) -> str:
    header = "| " + " | ".join(f"Col{i}" for i in range(n_cols)) + " |"
    sep = "| " + " | ".join("---" for _ in range(n_cols)) + " |"
    rows = [
        "| " + " | ".join(f"cell{r}_{c}" for c in range(n_cols)) + " |" for r in range(n_data_rows)
    ]
    return "\n".join([header, sep, *rows])


def test_segment_table_nodes_is_path_agnostic_and_idempotent():
    """RFC-033 D6 (Design Property 6): a tree with a single large TABLE node --
    as produced by the primary tree-build path after split_oversized_leaf_nodes
    -- is split into per-section sub-nodes exactly as on the garble-recovery
    paths; and because segmentation is idempotent, a document already
    segmented on a recovery path is byte-identical after the extra
    primary-path call D6 adds."""
    tarif_table = _pipe_table(n_data_rows=30)
    price_table = _pipe_table(n_data_rows=30)
    table_text = tarif_table + "\n\nAnhang\n\n" + price_table
    assert len(table_text) > 2000

    node = _segment_table_nodes([{"title": "GHV-TKV-Tarif", "text": table_text}])[0]
    assert node["text"] == ""
    children = node.get("nodes", [])
    assert len(children) >= 2
    assert any("|" in c["text"] for c in children)

    prose = "Paragraph text. " * 200
    recovered = [{"title": "Garbled Doc Section", "text": prose + "\n" + _pipe_table(20)}]
    pre_fix_output = _segment_table_nodes(recovered)
    snapshot = copy.deepcopy(pre_fix_output)
    assert _segment_table_nodes(pre_fix_output) == snapshot


# ---------------------------------------------------------------------------
# RFC-033 D7 — bare image extension forces content_class='image_standalone'
# ---------------------------------------------------------------------------
#
# index()'s flat-success branch calls
# client.apply_image_ext_content_class_override(ext, content_class) right after
# route_and_extract_flat. These tests drive the real
# index() coroutine (following the harness style of
# tests/test_rfc021_qf2a_lt.py) rather than re-implementing the conditional in
# the test file: a mirrored copy of the `if` would keep passing even if the
# production override were deleted, which is exactly the regression Property 7
# exists to catch.
#
# Reachability note: every intermediate branch between the _IMAGE_EXTS
# extraction route (client.py ~970) and the override (client.py ~1620) --
# OCR escalation (~1061), RTL repair (~1259), VLM fallback (~1286),
# image-dominant escalation (~1372) -- is gated on `ext == ".pdf"`, so a bare
# .jpg whose tree is rejected with depth<2 falls straight through to flat
# routing and reaches the override.


def _fake_index_settings():
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
        vlm_describe_images=False,
        pii_corpus=False,
    )


# Long enough to clear the flat-path garble gate and the D8a
# MIN_STANDALONE_IMAGE_MD_CHARS threshold with ordinary German prose.
_OCR_MD = (
    "<!-- image -->\n\n"
    "Verteilung der Beitraege nach Tarifgruppe im Geschaeftsjahr. "
    "Die Grafik zeigt den Anteil der einzelnen Sparten am Gesamtbestand. "
    "Weitere Angaben finden sich im Anhang zu diesem Bericht.\n"
)

# One image block + one prose block: route_and_extract_flat reports flat_mixed
# and the all-blocks-are-image heuristic at client.py:1605 does NOT fire, so
# only the D7 extension override can promote the content_class.
_MIXED_BLOCKS = [
    {"role": "image", "index": 0, "ocr_text": "Beitraege nach Tarifgruppe"},
    {"role": "prose", "index": 1, "text": "Die Grafik zeigt den Anteil der Sparten."},
]


async def _tree_coro():
    return {"structure": [{"node_id": "n1", "text": "x", "nodes": []}], "doc_description": ""}


def _tree_result():
    return _tree_coro()


def _wire_flat_route(monkeypatch, *, content_class, blocks):
    """Monkeypatch index()'s collaborators so it runs offline (no Docling,
    Tesseract, MinIO or LLM) and lands on the flat-success branch via a
    `depth<2` tree rejection."""
    fake_settings = _fake_index_settings()
    monkeypatch.setattr(_idx, "settings", fake_settings)
    monkeypatch.setattr(_img, "settings", fake_settings)
    monkeypatch.setattr(_idx, "hash_cache_get", lambda filename: None)
    monkeypatch.setattr(_idx, "list_processed_docs", lambda: [])
    monkeypatch.setattr(_idx, "hash_cache_set", MagicMock())
    monkeypatch.setattr(_idx, "validate_tree", lambda structure, **kw: (False, "depth<2"))
    monkeypatch.setattr(_idx, "ensure_tessdata", lambda langs: langs)
    monkeypatch.setattr(_idx, "image_to_markdown", lambda path, langs: _OCR_MD)
    monkeypatch.setattr(_idx, "_tesseract_ocr_image", lambda path, langs: _OCR_MD)
    monkeypatch.setattr(
        _idx, "pdf_markdown_converters", lambda: [("docling", lambda p, **kw: _OCR_MD, True)]
    )
    monkeypatch.setattr(_idx, "prepare_tree", lambda structure, **kw: structure)
    monkeypatch.setattr(_img, "_enrich_image_blocks", AsyncMock(return_value=None))
    monkeypatch.setattr(_idx, "_generate_flat_doc_description", lambda *a, **k: "desc")

    mocks = {
        "save_doc": MagicMock(),
        "save_flat_doc": MagicMock(),
        "save_raw": MagicMock(),
        "save_doc_meta": MagicMock(),
        "route_and_extract_flat": MagicMock(
            return_value=(content_class, [dict(b) for b in blocks])
        ),
        "FLAT_DOCS_TOTAL": MagicMock(),
        "LOW_QUALITY_TREES": MagicMock(),
        "OCR_ESCALATION_TOTAL": MagicMock(),
    }
    _mock_targets = {
        "save_doc": (_idx,),
        "save_flat_doc": (_idx,),
        "save_raw": (_idx,),
        "save_doc_meta": (_idx,),
        "route_and_extract_flat": (_img,),
        "FLAT_DOCS_TOTAL": (_idx,),
        "LOW_QUALITY_TREES": (_idx, _img),
        "OCR_ESCALATION_TOTAL": (_rec,),
    }
    for name, m in mocks.items():
        for mod in _mock_targets[name]:
            monkeypatch.setattr(mod, name, m)
    return mocks


@pytest.fixture
def jpg_file():
    fd, path = tempfile.mkstemp(suffix=".jpg")
    with os.fdopen(fd, "wb") as fh:
        fh.write(b"\xff\xd8\xff\xe0jpeg-ish-bytes")
    yield path
    if os.path.exists(path):
        os.unlink(path)


async def test_bare_jpg_extension_overrides_flat_mixed_to_image_standalone(monkeypatch, jpg_file):
    """D7: a .jpg whose OCR markdown route_and_extract_flat classifies as
    flat_mixed (an image block spliced together with prose) is force-overridden
    to content_class='image_standalone', so classify_verdict scores it via
    _classify_image_verdict instead of the flat_mixed char-floor promotion
    gate."""
    mocks = _wire_flat_route(monkeypatch, content_class="flat_mixed", blocks=_MIXED_BLOCKS)
    monkeypatch.setattr(_img, "_IMAGE_STANDALONE_PIPELINE_ENABLED", True)
    c = CustomPageIndexClient(api_key="test-key")
    monkeypatch.setattr(c, "_run_md_to_tree", lambda *a, **k: _tree_result())

    await c.index(jpg_file)

    mocks["route_and_extract_flat"].assert_called_once()
    assert mocks["save_flat_doc"].call_args.args[1]["content_class"] == "image_standalone"
    assert c.last_content_class == "image_standalone"


async def test_bare_jpg_override_respects_pipeline_kill_switch(monkeypatch, jpg_file):
    """D7 kill-switch: the override is guarded by
    _IMAGE_STANDALONE_PIPELINE_ENABLED. With the flag off, the same .jpg keeps
    the flat_mixed content_class route_and_extract_flat assigned it."""
    mocks = _wire_flat_route(monkeypatch, content_class="flat_mixed", blocks=_MIXED_BLOCKS)
    monkeypatch.setattr(_img, "_IMAGE_STANDALONE_PIPELINE_ENABLED", False)
    c = CustomPageIndexClient(api_key="test-key")
    monkeypatch.setattr(c, "_run_md_to_tree", lambda *a, **k: _tree_result())

    await c.index(jpg_file)

    assert mocks["save_flat_doc"].call_args.args[1]["content_class"] == "flat_mixed"


async def test_pdf_with_mixed_blocks_is_not_overridden_to_image_standalone(monkeypatch, pdf_file):
    """D7 negative case: '.pdf' is not in _IMAGE_EXTS, so a PDF whose
    route_and_extract_flat output is flat_mixed (mixed image/text blocks)
    keeps its original content_class. The extension override applies only to
    bare image files, not to PDFs that happen to contain images."""
    assert ".pdf" not in _IMAGE_EXTS
    mocks = _wire_flat_route(monkeypatch, content_class="flat_mixed", blocks=_MIXED_BLOCKS)
    monkeypatch.setattr(_img, "_IMAGE_STANDALONE_PIPELINE_ENABLED", True)
    c = CustomPageIndexClient(api_key="test-key")
    monkeypatch.setattr(c, "_run_md_to_tree", lambda *a, **k: _tree_result())

    await c.index(pdf_file)

    assert mocks["save_flat_doc"].call_args.args[1]["content_class"] == "flat_mixed"


# ---------------------------------------------------------------------------
# Zone-8: wiring test — _IMAGE_EXTS and MIN_STANDALONE_IMAGE_MD_CHARS
# imported from images.py in indexer.py (no local redefinition)
# ---------------------------------------------------------------------------


class TestImageConstantsWiring:
    """Zone-8 wiring: _IMAGE_EXTS and MIN_STANDALONE_IMAGE_MD_CHARS are
    imported from images.py (canonical source), not redefined in indexer.py.
    Changing images.MIN_STANDALONE_IMAGE_MD_CHARS must affect indexer behavior."""

    def test_image_and_supported_constants_are_canonical(self, monkeypatch):
        # _IMAGE_EXTS in indexer.py must be the *same object* as in images.py,
        # and MIN_STANDALONE_IMAGE_MD_CHARS the same value.
        assert _idx._IMAGE_EXTS is _img._IMAGE_EXTS
        assert _idx._IMAGE_EXTS == {".png", ".jpg", ".jpeg", ".tiff", ".tif"}
        original = _idx.MIN_STANDALONE_IMAGE_MD_CHARS
        assert original == _img.MIN_STANDALONE_IMAGE_MD_CHARS

        # indexer.py does `from .images import _IMAGE_EXTS, ...,
        # MIN_STANDALONE_IMAGE_MD_CHARS`, so indexer's name is a module-level
        # binding that can be overridden for testing without touching images.
        monkeypatch.setattr(_idx, "MIN_STANDALONE_IMAGE_MD_CHARS", 999)
        assert _idx.MIN_STANDALONE_IMAGE_MD_CHARS == 999
        assert _img.MIN_STANDALONE_IMAGE_MD_CHARS == original

        # The upload/ingest extension allowlist is the canonical _SUPPORTED set.
        assert {".pdf", ".md", ".docx", ".pptx", ".html", ".txt"}.issubset(_SUPPORTED)
        assert ".markdown" in _SUPPORTED


# ===========================================================================
# RAG contract, deduplication, and pagination  (helpers.rag / tools.documents)
# ===========================================================================


async def test_rag_01_c3_no_documents_raises_tool_error():
    """RAG-01-C3: find_relevant_documents() with zero indexed docs raises a
    ToolError (isError:true) carrying reason=verdict_fail, and never runs a
    tree-search LLM call. Exercises the real tool entry point."""
    from fastmcp.exceptions import ToolError

    # RFC-009 D6: registry-only read path — the empty corpus is an empty registry
    # listing (registry.list_docs -> []), not an empty MinIO scan.
    with (
        patch.object(documents, "_require_registry_ready", new=AsyncMock(return_value=None)),
        patch("pageindex_mcp.registry.list_docs", new=AsyncMock(return_value=[])),
        patch("pageindex_mcp.helpers.rag._llm", new_callable=AsyncMock) as mock_llm,
        pytest.raises(ToolError, match="verdict_fail"),
    ):
        await find_relevant_documents("any query")

    # No LLM tree-search call was issued on the empty-corpus path.
    mock_llm.assert_not_called()


async def test_rag_01_c1_c2_prefilter_then_bounded_fanout():
    """RAG-01-C1: _rag prefilters candidate docs first, so a doc the prefilter
    drops is never handed to the per-doc tree search.
    RAG-01-C2: the surviving candidates fan out one task each, all bounded by
    an asyncio.Semaphore of size PAGEINDEX_SEARCH_CONCURRENCY."""
    from pageindex_mcp import helpers

    def _doc(i):
        return {
            "doc_name": f"{i}.pdf",
            "doc_description": str(i),
            "structure": [{"node_id": f"n{i}", "title": str(i), "summary": "s", "text": "t"}],
        }

    # --- C1: the excluded doc is never searched -----------------------------
    store = {"aaa": _doc("a"), "bbb": _doc("b")}
    searched: list[str] = []

    async def record_search(query, doc_id, data, semaphore):
        searched.append(doc_id)
        return None  # no matched text; we only care about WHICH docs are searched

    with (
        patch("pageindex_mcp.helpers.rag.get_doc", side_effect=lambda d: store[d]),
        patch(
            "pageindex_mcp.helpers.rag._prefilter_docs", new=AsyncMock(return_value=["aaa"])
        ) as mock_prefilter,
        patch("pageindex_mcp.helpers.rag._search_one_doc", side_effect=record_search),
    ):
        await helpers._rag("q", ["aaa", "bbb"])

    mock_prefilter.assert_awaited_once()
    assert searched == ["aaa"], "prefilter-excluded doc must never be searched"

    # --- C2: fan-out is bounded by the search-concurrency semaphore ---------
    big_store = {f"d{i}": _doc(i) for i in range(6)}
    doc_ids = list(big_store.keys())
    inflight = 0
    max_inflight = 0
    seen: set[str] = set()

    async def bounded_search(query, doc_id, data, semaphore):
        nonlocal inflight, max_inflight
        async with semaphore:
            inflight += 1
            max_inflight = max(max_inflight, inflight)
            await asyncio.sleep(0)  # yield so overlap can occur
            seen.add(doc_id)
            inflight -= 1
        return None

    with (
        patch("pageindex_mcp.helpers.rag.get_doc", side_effect=lambda d: big_store[d]),
        patch("pageindex_mcp.helpers.rag._prefilter_docs", new=AsyncMock(return_value=doc_ids)),
        patch("pageindex_mcp.helpers.rag._search_one_doc", side_effect=bounded_search),
        patch("pageindex_mcp.helpers.rag._SEARCH_CONCURRENCY", 2),
    ):
        await helpers._rag("q", doc_ids)

    assert seen == set(doc_ids), "every prefiltered doc must be searched"
    assert 1 <= max_inflight <= 2, f"concurrency bound violated: max_inflight={max_inflight}"


async def test_find_relevant_documents_loads_each_doc_once():
    """get_doc should be called once per doc during RAG, not twice (once in
    the listing and again in _rag)."""
    fake_meta = [
        {"doc_id": "aaa11111", "doc_name": "a.pdf", "source_url": "", "processed_at": ""},
    ]
    fake_doc = {
        "doc_name": "a.pdf",
        "doc_description": "",
        "structure": [
            {
                "node_id": "n1",
                "title": "Intro",
                "summary": "intro",
                "text": "hello",
                "start_index": 1,
                "end_index": 1,
            },
        ],
    }

    with (
        patch.object(documents, "_require_registry_ready", new=AsyncMock(return_value=None)),
        patch("pageindex_mcp.registry.list_docs", new=AsyncMock(return_value=fake_meta)),
        patch("pageindex_mcp.helpers.rag.get_doc", return_value=fake_doc) as mock_load,
        patch("pageindex_mcp.helpers.rag._llm", new_callable=AsyncMock) as mock_llm,
    ):
        mock_llm.side_effect = [
            '{"thinking": "relevant", "node_list": ["n1"]}',
            "The answer is hello.",
        ]
        await find_relevant_documents("test query")

    assert mock_load.call_count == 1


def _row(i: int, node_count: int | None = 7) -> dict:
    return {
        "doc_id": f"doc-{i:02d}",
        "doc_name": f"Document {i:02d}",
        "source_url": "",
        "processed_at": "",
        "content_class": "",
        "node_count": node_count,
    }


async def test_recent_documents_pages_via_sql_limit_offset():
    """page=2, page_size=5 must reach registry.list_docs as limit=5, offset=5 —
    never the old limit=100_000 fetch-all-then-slice — and real LIMIT/OFFSET
    semantics over a 20-doc corpus return exactly that window."""
    list_docs = AsyncMock(return_value=[_row(i) for i in range(6, 11)])
    count_docs = AsyncMock(return_value=20)

    with (
        patch.object(documents, "_require_registry_ready", new=AsyncMock(return_value=None)),
        patch("pageindex_mcp.registry.list_docs", new=list_docs),
        patch("pageindex_mcp.registry.count_docs", new=count_docs),
    ):
        result = await documents.recent_documents(page=2, page_size=5)

    list_docs.assert_awaited_once_with(limit=5, offset=5)
    assert list_docs.await_args.kwargs != {"limit": 100_000, "offset": 0}
    payload = json.loads(result)
    assert payload["page"] == 2
    assert payload["page_size"] == 5
    assert payload["total"] == 20  # count_docs, not the 5-row slice
    assert len(payload["documents"]) == 5

    corpus = [_row(i) for i in range(1, 21)]  # doc-01 .. doc-20

    async def fake_list_docs(limit: int, offset: int):
        return corpus[offset : offset + limit]

    async def fake_count_docs():
        return len(corpus)

    with (
        patch.object(documents, "_require_registry_ready", new=AsyncMock(return_value=None)),
        patch("pageindex_mcp.registry.list_docs", new=fake_list_docs),
        patch("pageindex_mcp.registry.count_docs", new=fake_count_docs),
    ):
        window = json.loads(await documents.recent_documents(page=2, page_size=5))

    assert window["total"] == 20
    assert [d["doc_id"] for d in window["documents"]] == [
        "doc-06",
        "doc-07",
        "doc-08",
        "doc-09",
        "doc-10",
    ]


async def test_recent_documents_node_count_read_from_row_never_via_get_doc():
    """node_count must be read from the listing row, so get_doc() (tree
    deserialization) is never invoked on the paginated read path. Legacy rows
    predating the D2 backfill carry node_count=None and surface as 0."""
    get_doc = MagicMock()

    for rows, expected in (
        ([_row(i, node_count=42) for i in range(1, 4)], [42, 42, 42]),
        ([_row(1, node_count=None)], [0]),
    ):
        get_doc.reset_mock()
        with (
            patch.object(documents, "_require_registry_ready", new=AsyncMock(return_value=None)),
            patch("pageindex_mcp.registry.list_docs", new=AsyncMock(return_value=rows)),
            patch("pageindex_mcp.registry.count_docs", new=AsyncMock(return_value=len(rows))),
            patch.object(documents, "get_doc", new=get_doc),
        ):
            payload = json.loads(await documents.recent_documents(page=1, page_size=10))

        get_doc.assert_not_called()
        assert [d["node_count"] for d in payload["documents"]] == expected


async def test_registry_path_returns_correct_results():
    """Healthy registry -> recent_documents returns the correct listing straight
    from a single SQL query (limit/offset + count), no MinIO involvement."""
    rows = [_row(i) for i in range(1, 4)]

    with (
        patch.object(documents, "_require_registry_ready", new=AsyncMock(return_value=None)),
        patch("pageindex_mcp.registry.list_docs", new=AsyncMock(return_value=rows)),
        patch("pageindex_mcp.registry.count_docs", new=AsyncMock(return_value=3)),
    ):
        payload = json.loads(await documents.recent_documents(page=1, page_size=10))

    assert payload["total"] == 3
    assert [d["doc_id"] for d in payload["documents"]] == ["doc-01", "doc-02", "doc-03"]
    assert all(d["status"] == "completed" for d in payload["documents"])


async def test_registry_unavailable_never_falls_back_to_minio():
    """RFC-009 D6 (Property 7): with the MinIO fallback gone, every
    registry-unavailable condition -- a count_docs failure, Postgres down, and
    a disabled registry -- must surface isError:true from both listing tools
    (Phase 3 audit Issue B) and never call storage.list_processed_docs()."""
    from fastmcp.exceptions import ToolError

    import pageindex_mcp.storage as storage_mod
    from pageindex_mcp.tools.documents import RegistryUnavailableError

    # 1. list_docs succeeds but count_docs errors (None) -> no MinIO-derived total.
    minio = MagicMock()
    with (
        patch.object(documents, "_require_registry_ready", new=AsyncMock(return_value=None)),
        patch(
            "pageindex_mcp.registry.list_docs",
            new=AsyncMock(return_value=[_row(i) for i in range(1, 6)]),
        ),
        patch("pageindex_mcp.registry.count_docs", new=AsyncMock(return_value=None)),
        patch("pageindex_mcp.storage.list_processed_docs", new=minio),
        pytest.raises(ToolError, match="registry unavailable"),
    ):
        await documents.recent_documents(page=1, page_size=5)
    minio.assert_not_called()

    # 2. Postgres down: list_docs returns None on a query failure.
    minio = MagicMock()
    with (
        patch.object(documents, "_require_registry_ready", new=AsyncMock(return_value=None)),
        patch("pageindex_mcp.registry.list_docs", new=AsyncMock(return_value=None)),
        patch("pageindex_mcp.registry.count_docs", new=AsyncMock(return_value=None)),
        patch("pageindex_mcp.storage.list_processed_docs", new=minio),
    ):
        with pytest.raises(ToolError, match="registry unavailable"):
            await documents.recent_documents(page=1, page_size=5)
        with pytest.raises(ToolError, match="registry unavailable"):
            await documents.find_relevant_documents("q")
    minio.assert_not_called()

    # 3. Registry disabled entirely (the standard disabled error shape).
    async def _registry_disabled(*_args, **_kwargs):
        raise RegistryUnavailableError("disabled")

    with (
        patch.object(documents, "_require_registry_ready", side_effect=_registry_disabled),
        patch.object(storage_mod, "list_processed_docs") as disabled_minio,
    ):
        with pytest.raises(ToolError):
            await documents.recent_documents(page=1, page_size=5)
        with pytest.raises(ToolError):
            await documents.find_relevant_documents("q")
    disabled_minio.assert_not_called()


# ===========================================================================
# tools.documents not-found paths  (RFC-009 D1 / ISS-21)
# ===========================================================================


def test_query_path_not_found_never_lists_minio():
    """RFC-009 Design Property 1 -- "No O(N) listing on error paths".

    get_document, get_document_structure and get_page_content previously built
    an `available` array via list_processed_docs() (a full MinIO listing) on
    every doc_id miss, giving any invalid id a DoS-shaped cost. Each must now
    return exactly {"error": "Document not found: <id>"} -- no `available` key
    -- without touching storage at all.
    """

    def _not_found(_doc_id):
        raise KeyError("doc not found")

    doc_id = "nonexistent-doc-id"
    calls = [
        ("get_document", lambda: documents.get_document(doc_id)),
        ("get_document_structure", lambda: documents.get_document_structure(doc_id)),
        ("get_page_content", lambda: documents.get_page_content(doc_id, "1-3")),
    ]

    failures = []
    for name, call in calls:
        with (
            patch("pageindex_mcp.tools.documents.get_doc", side_effect=_not_found),
            patch("pageindex_mcp.storage.list_processed_docs") as mock_list,
        ):
            body = json.loads(call())
        if body != {"error": f"Document not found: {doc_id}"}:
            failures.append(f"  [{name}] unexpected body: {body}")
        if mock_list.called:
            failures.append(f"  [{name}] called list_processed_docs on the not-found path")
    assert not failures, "not-found contract violations:\n" + "\n".join(failures)


# ===========================================================================
# get_document(include=...)  (RFC-050 D3, slimmed — raw-markdown sidecar)
# ===========================================================================


class TestGetDocumentIncludeParam:
    """include="" is byte-identical to the pre-existing behaviour; include=
    "raw" adds raw_markdown; any other value is the tool's usual error shape.
    """

    _TREE_DOC = {
        "doc_id": "d1",
        "doc_name": "report.pdf",
        "structure": [{"title": "A", "node_id": "node_001", "start_index": 1, "end_index": 2}],
    }

    def test_include_param_matrix(self):
        flat_doc = {"doc_id": "d2", "doc_name": "flat.pdf", "content_class": "flat_prose"}
        fake_flat_view = {
            "doc_name": "flat.pdf",
            "content_class": "flat_prose",
            "blocks": [],
            "row_records": [],
        }
        failures = []

        # Default: include="" is byte-identical to omitting it, no raw field.
        with patch("pageindex_mcp.tools.documents.get_doc", return_value=self._TREE_DOC):
            explicit_default = documents.get_document("d1", include="")
            omitted = documents.get_document("d1")
            invalid = json.loads(documents.get_document("d1", include="bogus"))
        if explicit_default != omitted or "raw_markdown" in explicit_default:
            failures.append("default: not byte-identical to no include")
        # Invalid value -> the tool's usual error shape.
        if "error" not in invalid or "raw_markdown" in invalid:
            failures.append(f"invalid: unexpected body {invalid!r}")

        cases = [
            # name, doc, sidecar, expected raw_markdown, expect note
            ("raw, tree, sidecar present", self._TREE_DOC, "# extracted", "# extracted", False),
            ("raw, tree, sidecar absent", self._TREE_DOC, None, None, True),
            ("raw, flat, sidecar present", flat_doc, "flat markdown", "flat markdown", False),
        ]
        for name, doc, sidecar, expected, expect_note in cases:
            with (
                patch("pageindex_mcp.tools.documents.get_doc", return_value=doc),
                patch("pageindex_mcp.tools.documents.flat_doc_view", lambda d: fake_flat_view),
                patch("pageindex_mcp.storage.documents.load_extracted_md", return_value=sidecar),
            ):
                body = json.loads(documents.get_document(doc["doc_id"], include="raw"))
            if body.get("raw_markdown", "<missing>") != expected:
                failures.append(f"{name}: raw_markdown={body.get('raw_markdown', '<missing>')!r}")
            if bool(body.get("raw_markdown_note")) is not expect_note:
                failures.append(f"{name}: raw_markdown_note={body.get('raw_markdown_note')!r}")
        assert not failures, failures


# ===========================================================================
# storage.documents.load_extracted_md  (RFC-050 D3, slimmed)
# ===========================================================================


class TestLoadExtractedMd:
    """The loader only ever reads uploads/<doc_id>/*, never quarantine/, and
    rejects malformed doc_ids without touching MinIO."""

    def test_loader_reads_uploads_only_and_validates_doc_id(self):
        from pageindex_mcp.storage.documents import load_extracted_md

        # Path-traversal doc_id -> None without touching MinIO.
        with patch("pageindex_mcp.storage.documents._minio_ops.get_minio") as mock_get_minio:
            assert load_extracted_md("../../etc/passwd") is None
        mock_get_minio.assert_not_called()

        # Only uploads/<doc_id>/ is listed and read, never quarantine/.
        mock_mc = MagicMock()

        def _list_objects(bucket, prefix, recursive=True):
            assert prefix.startswith("uploads/"), f"loader listed non-uploads prefix: {prefix}"
            assert "quarantine" not in prefix
            obj = MagicMock()
            obj.object_name = f"{prefix}report.pdf.extracted.md"
            return iter([obj])

        mock_mc.list_objects.side_effect = _list_objects
        resp = MagicMock()
        resp.read.return_value = b"raw text"
        mock_mc.get_object.return_value = resp

        with patch("pageindex_mcp.storage.documents._minio_ops.get_minio", return_value=mock_mc):
            assert load_extracted_md("doc-123") == "raw text"
        mock_mc.get_object.assert_called_once()
        called_key = mock_mc.get_object.call_args.args[1]
        assert called_key.startswith("uploads/doc-123/")
        assert "quarantine" not in called_key

        # No .extracted.md object under the prefix -> None.
        mock_mc = MagicMock()
        obj = MagicMock()
        obj.object_name = "uploads/doc-123/report.pdf"
        mock_mc.list_objects.return_value = iter([obj])
        with patch("pageindex_mcp.storage.documents._minio_ops.get_minio", return_value=mock_mc):
            assert load_extracted_md("doc-123") is None


# ===========================================================================
# Erasure cascade: validate_erasure_manifest + delete_doc logging
# ===========================================================================


class TestValidateErasureManifest:
    """Contract tests for validate_erasure_manifest()."""

    def test_every_registered_prefix_maps_to_a_real_erasure_step(self):
        """The shipped code passes; a registered prefix with neither a
        _PREFIX_TO_ERASURE_STEPS entry nor a matching ErasureStep raises
        ImportError, as does a mapping entry naming a step that is not in
        _ERASURE_MANIFEST."""
        import pageindex_mcp.storage.documents as _docs_mod

        validate_erasure_manifest()  # shipped state: must not raise

        augmented = _KNOWN_STORAGE_PREFIXES | {"staging/"}
        with patch.object(_docs_mod, "_KNOWN_STORAGE_PREFIXES", augmented):
            with pytest.raises(ImportError, match="staging/"):
                validate_erasure_manifest()

        extra_prefixes = _KNOWN_STORAGE_PREFIXES | {"drafts/"}
        extra_mapping = dict(_PREFIX_TO_ERASURE_STEPS)
        extra_mapping["drafts/"] = ("drafts_cleanup",)
        with (
            patch.object(_docs_mod, "_KNOWN_STORAGE_PREFIXES", extra_prefixes),
            patch.object(_docs_mod, "_PREFIX_TO_ERASURE_STEPS", extra_mapping),
        ):
            with pytest.raises(ImportError, match="drafts_cleanup"):
                validate_erasure_manifest()

    def test_step_ordering_guards_fail_loudly_at_import_time(self):
        """RFC-043 D4: a step consuming a ctx.* field must come after its
        producer, and a step reading a sidecar must come before the step that
        deletes it. Either reordering must fail loudly."""
        import pageindex_mcp.storage.documents as _docs_mod

        reordered = (
            ErasureStep(
                name="early_consumer",
                step=0,
                description="test",
                execute=AsyncMock(return_value=True),
                consumes=frozenset({"ctx.doc_name"}),
            ),
        ) + tuple(s for s in _ERASURE_MANIFEST if s.name != "early_consumer")
        with patch.object(_docs_mod, "_ERASURE_MANIFEST", reordered):
            with pytest.raises(ValueError, match="early_consumer.*ctx.doc_name"):
                validate_erasure_manifest()

        by_name = {s.name: s for s in _ERASURE_MANIFEST}
        swapped = tuple(
            by_name["meta_json"]
            if name == "verdicts"
            else by_name["verdicts"]
            if name == "meta_json"
            else by_name[name]
            for name in by_name
        )
        with patch.object(_docs_mod, "_ERASURE_MANIFEST", swapped):
            with pytest.raises(ValueError, match="verdicts.*processed/\\{id\\}\\.meta\\.json"):
                validate_erasure_manifest()


def _s3_no_such_key(*args, **kwargs):
    raise S3Error("NoSuchKey", "Not found", "", "", "", "")


def _s3_error(code):
    def _raise(*args, **kwargs):
        raise S3Error(code, "error", "", "", "", "")

    return _raise


def _mock_settings(**overrides):
    """Build a settings-like object with sensible defaults for erasure tests."""
    import dataclasses

    from pageindex_mcp.config import settings as _base

    return dataclasses.replace(_base, **overrides)


class TestDeleteDocLogMessages:
    """Regression: delete_doc log message must distinguish full success from
    partial-optional skip (ctx.errors empty but optional stores missed)."""

    @pytest.mark.asyncio
    async def test_full_success_logs_required_ok_and_optional_skipped(self, caplog):
        """When all required steps succeed and some optional steps are skipped
        (no errors), the log must report both counts, not just 'full cascade
        succeeded'."""
        from pageindex_mcp.storage.documents import delete_doc

        # A mock MinIO client tolerating all remove/list calls. get_object
        # returns a meta.json with no sha256, so the verdicts step is skipped
        # (optional) without adding an error.
        mock_mc = MagicMock()

        def _get_object_side_effect(bucket, key):
            if key.endswith(".meta.json"):
                resp = MagicMock()
                resp.read.return_value = b'{"content_class": "tree"}'
                return resp
            raise S3Error("NoSuchKey", "Not found", "", "", "", "")

        mock_mc.get_object.side_effect = _get_object_side_effect
        mock_mc.list_objects.return_value = iter([])
        mock_mc.remove_object.return_value = None

        mock_settings = _mock_settings(
            registry_enabled=True,
            postgres_dsn="postgresql://u:p@localhost/db",
        )

        with (
            patch("pageindex_mcp.storage.documents._minio_ops.get_minio", return_value=mock_mc),
            patch(
                "pageindex_mcp.storage.documents.load_doc",
                return_value={"doc_name": "test.pdf"},
            ),
            patch("pageindex_mcp.cache.doc_cache_delete", return_value=None),
            patch(
                "pageindex_mcp.storage.reconcile_etag.reconcile_etag_delete",
                return_value=None,
            ),
            patch(
                "pageindex_mcp.storage.hash_cache.hash_cache_delete",
                return_value=None,
            ),
            patch("pageindex_mcp.storage.documents.settings", mock_settings),
            patch("pageindex_mcp.registry.get_pool", return_value=object()),
            patch("pageindex_mcp.registry.delete_doc", AsyncMock(return_value=None)),
            caplog.at_level(logging.INFO, logger="pageindex_mcp.storage.documents"),
        ):
            result = await delete_doc("test-doc-1")

        assert result["errors"] == [], f"Expected no errors but got: {result['errors']}"
        cascade_msgs = [r.message for r in caplog.records if "cascade complete" in r.message]
        assert len(cascade_msgs) >= 1, (
            f"Expected 'cascade complete' log but got: {[r.message for r in caplog.records]}"
        )
        assert "required ok" in cascade_msgs[0]
        assert "optional skipped" in cascade_msgs[0]

    @pytest.mark.asyncio
    async def test_step1_failure_logs_partial_failure_and_reports_partial_purge(self, caplog):
        """RFC-043 D5: when step 1 (uploads) fails, ctx.errors is non-empty so
        delete_doc logs 'partial failure' rather than 'cascade complete'; and
        because doc_name is never recovered, the doc_name-dependent optional
        steps are skipped and partial_purge is True."""
        from pageindex_mcp.storage.documents import delete_doc

        mock_mc = MagicMock()
        # uploads listing raises -> step 1 fails, doc_name never recovered
        mock_mc.list_objects.side_effect = _s3_error("InternalError")
        mock_mc.get_object.side_effect = _s3_no_such_key
        mock_mc.remove_object.return_value = None

        with (
            patch("pageindex_mcp.storage.documents._minio_ops.get_minio", return_value=mock_mc),
            patch("pageindex_mcp.storage.documents.load_doc", side_effect=ValueError("gone")),
            patch("pageindex_mcp.cache.doc_cache_delete", return_value=None),
            patch(
                "pageindex_mcp.storage.reconcile_etag.reconcile_etag_delete",
                return_value=None,
            ),
            patch(
                "pageindex_mcp.storage.documents.settings",
                _mock_settings(registry_enabled=False, postgres_dsn=""),
            ),
            caplog.at_level(logging.ERROR, logger="pageindex_mcp.storage.documents"),
        ):
            result = await delete_doc("test-doc-err")

        assert len(result["errors"]) > 0
        assert result["partial_purge"] is True
        assert [r.message for r in caplog.records if "partial failure" in r.message]


# ===========================================================================
# _persist_tree_result / _persist_flat_result raw-markdown sidecar
# (RFC-050 D3, slimmed)
# ===========================================================================


def _make_persist_state(*, md_content):
    from pageindex_mcp.helpers import ExtractionState, Route, TreeDefect

    return ExtractionState(
        result={"structure": [], "doc_description": ""},
        ok=True,
        reason="",
        gate_result=None,
        first_defect=TreeDefect.NODE_COUNT_LOW,
        route=Route.TREE,
        md_content=md_content,
        tmp_md_path=None,
        pic_results=[],
        used_converter=None,
        total_chars=0,
        extraction_stages_captured=[],
    )


class TestPersistTreeResultRawMarkdownSidecar:
    @pytest.mark.asyncio
    async def test_extracted_md_sidecar_written_skipped_and_best_effort(self, caplog):
        """Writes <name>.extracted.md when md_content is set, skips it when
        None, and a failing sidecar write never breaks the persist."""

        def _fail_sidecar(doc_id, filename, data):
            if filename.endswith(".extracted.md"):
                raise RuntimeError("minio down")

        async def _run(md_content, save_raw_side_effect=None):
            state = _make_persist_state(md_content=md_content)
            client = CustomPageIndexClient.__new__(CustomPageIndexClient)
            with (
                patch.object(_idx, "save_doc"),
                patch.object(_idx, "save_doc_meta"),
                patch.object(_idx, "save_raw", side_effect=save_raw_side_effect) as mock_save_raw,
                patch.object(_idx, "hash_cache_set"),
                patch.object(_idx, "compute_verdict") as mock_verdict,
            ):
                mock_verdict.return_value.verdict = "PASS"
                mock_verdict.return_value.reason = "ok"
                mock_verdict.return_value.promotion_paths_matched = []
                doc_id = await client._persist_tree_result(
                    state, "report.pdf", ".pdf", None, "deadbeef" * 8, b"bytes", None, {}, None
                )
            return doc_id, mock_save_raw

        # md_content set -> raw upload + sidecar.
        doc_id, mock_save_raw = await _run("# extracted body")
        assert mock_save_raw.call_count == 2
        raw_call, md_call = mock_save_raw.call_args_list
        assert raw_call.args == (doc_id, "report.pdf", b"bytes")
        assert md_call.args == (doc_id, "report.pdf.extracted.md", b"# extracted body")

        # md_content None -> raw upload only.
        _doc_id, mock_save_raw = await _run(None)
        assert mock_save_raw.call_count == 1

        # Sidecar write failure -> persist still returns a doc_id, with a warning.
        with caplog.at_level(logging.WARNING, logger="pageindex_mcp.client.indexer"):
            doc_id, _ = await _run("# extracted body", _fail_sidecar)
        assert isinstance(doc_id, str) and doc_id
        assert any("extracted.md sidecar" in r.message for r in caplog.records)


class TestPersistFlatResultRawMarkdownSidecar:
    """Same contract, driven through the flat persist path."""

    def _make_flat_state(self, *, md_content, tmp_md_path=None):
        from pageindex_mcp.helpers import ExtractionState, Route, TreeDefect

        return ExtractionState(
            result={"structure": []},
            ok=True,
            reason="",
            gate_result=None,
            first_defect=TreeDefect.NODE_COUNT_LOW,
            route=Route.FLAT,
            md_content=md_content,
            tmp_md_path=tmp_md_path,
            pic_results=[],
            used_converter=None,
            total_chars=0,
            extraction_stages_captured=[],
        )

    async def _run(self, monkeypatch, state, save_raw_side_effect=None):
        monkeypatch.setattr(_idx, "route_and_extract_flat", lambda md: ("flat_prose", []))
        monkeypatch.setattr(_idx, "_garble_check_flat_blocks", lambda blocks, **kw: None)
        monkeypatch.setattr(
            _idx,
            "_apply_picture_enrichment",
            AsyncMock(return_value=("doc-flat-1", "flat_prose", [], 0.0)),
        )
        mock_verdict = MagicMock()
        mock_verdict.return_value.verdict = "PASS"
        mock_verdict.return_value.reason = "ok"
        mock_verdict.return_value.promotion_paths_matched = []
        monkeypatch.setattr(_idx, "compute_verdict", mock_verdict)
        monkeypatch.setattr(_idx, "_generate_flat_doc_description", lambda md, **kw: "desc")
        monkeypatch.setattr(_idx, "save_flat_doc", MagicMock())
        monkeypatch.setattr(_idx, "save_doc_meta", MagicMock())
        mock_save_raw = MagicMock(side_effect=save_raw_side_effect)
        monkeypatch.setattr(_idx, "save_raw", mock_save_raw)
        monkeypatch.setattr(_idx, "hash_cache_set", MagicMock())
        monkeypatch.setattr(_idx, "clear_quarantine", MagicMock())
        monkeypatch.setattr(_idx, "FLAT_DOCS_TOTAL", MagicMock())

        client = CustomPageIndexClient.__new__(CustomPageIndexClient)
        doc_id = await client._persist_flat_result(
            state,
            "/tmp/report.pdf",
            "report.pdf",
            ".pdf",
            None,
            "deadbeef" * 8,
            b"bytes",
            None,
            {},
            None,
        )
        return doc_id, mock_save_raw

    @pytest.mark.asyncio
    async def test_extracted_md_sidecar_written_skipped_and_best_effort(
        self, monkeypatch, tmp_path, caplog
    ):
        # md_content set -> raw upload + sidecar.
        state = self._make_flat_state(md_content="# flat extracted body")
        doc_id, mock_save_raw = await self._run(monkeypatch, state)
        assert mock_save_raw.call_count == 2
        raw_call, md_call = mock_save_raw.call_args_list
        assert raw_call.args == (doc_id, "report.pdf", b"bytes")
        assert md_call.args == (doc_id, "report.pdf.extracted.md", b"# flat extracted body")

        # md_content is None but tmp_md_path resolves real markdown, so persist
        # still succeeds -- it should just skip the extracted.md sidecar write.
        tmp_md = tmp_path / "converted.md"
        tmp_md.write_text("body text from tmp_md_path", encoding="utf-8")
        state = self._make_flat_state(md_content=None, tmp_md_path=str(tmp_md))
        doc_id, mock_save_raw = await self._run(monkeypatch, state)
        assert isinstance(doc_id, str) and doc_id
        assert mock_save_raw.call_count == 1
        assert mock_save_raw.call_args.args == (doc_id, "report.pdf", b"bytes")

        # Sidecar write failure -> persist still returns a doc_id, with a warning.
        def _fail_sidecar(doc_id, filename, data):
            if filename.endswith(".extracted.md"):
                raise RuntimeError("minio down")

        state = self._make_flat_state(md_content="# flat extracted body")
        with caplog.at_level(logging.WARNING, logger="pageindex_mcp.client.indexer"):
            doc_id, _ = await self._run(monkeypatch, state, _fail_sidecar)
        assert isinstance(doc_id, str) and doc_id
        assert any("extracted.md sidecar" in r.message for r in caplog.records)


# ===========================================================================
# helpers.heuristic_registry  (RFC-041 D5 — Property 5)
# ===========================================================================


class TestHeuristicRegistryCore:
    """register / get / fire / is_expired / list_expired and their metrics."""

    def test_register_stores_fields_and_defaults_expiry_to_90_days(self):
        r = HeuristicRegistry()
        entry = r.register("test_h", "RFC-099", created=date(2026, 9, 1), expiry=date(2026, 12, 1))
        assert isinstance(entry, HeuristicEntry)
        assert entry.name == "test_h"
        assert entry.rfc_origin == "RFC-099"
        assert entry.created == date(2026, 9, 1)
        assert entry.expiry == date(2026, 12, 1)

        default = r.register("test_default", "RFC-099", created=date(2026, 1, 1))
        assert default.expiry == date(2026, 1, 1) + timedelta(days=90)

        assert r.get("test_h") is not None
        assert r.get("missing") is None

    def test_fire_increments_counter_and_warns_on_expired_or_unregistered(self, caplog):
        r = HeuristicRegistry()
        r.register("counter_test", "RFC-099", created=date(2026, 9, 1), expiry=date(2027, 1, 1))
        before = _HEURISTIC_FIRE_COUNTER.labels(heuristic="counter_test")._value.get()
        r.fire("counter_test")
        after = _HEURISTIC_FIRE_COUNTER.labels(heuristic="counter_test")._value.get()
        assert after == before + 1

        r.register("expired_h", "RFC-099", created=date(2025, 1, 1), expiry=date(2025, 6, 1))
        with caplog.at_level(logging.WARNING, logger="pageindex_mcp.helpers.heuristic_registry"):
            r.fire("expired_h", ref_date=date(2026, 9, 1))
            r.fire("nonexistent")
        assert any(
            "expired heuristic" in rec.message and "expired_h" in rec.message
            for rec in caplog.records
        )
        assert any("unregistered" in rec.message for rec in caplog.records)

    def test_expiry_predicates_and_gauge(self):
        r = HeuristicRegistry()
        r.register("old_h", "RFC-099", created=date(2025, 1, 1), expiry=date(2025, 6, 1))
        r.register("new_h", "RFC-099", created=date(2026, 9, 1), expiry=date(2027, 1, 1))
        assert r.is_expired("old_h", ref_date=date(2026, 1, 1)) is True
        assert r.is_expired("new_h", ref_date=date(2026, 9, 1)) is False

        r.register("expired_h2", "RFC-099", created=date(2025, 1, 1), expiry=date(2025, 3, 1))
        names = {e.name for e in r.list_expired(ref_date=date(2026, 9, 1))}
        assert names == {"old_h", "expired_h2"}

        # The expired gauge is set at registration time, 1.0 for an already
        # expired heuristic and 0.0 for an active one.
        r.register("gauge_exp", "RFC-099", created=date(2025, 1, 1), expiry=date(2025, 6, 1))
        r.register("gauge_act", "RFC-099", created=date(2026, 9, 1), expiry=date(2027, 12, 1))
        assert _HEURISTIC_EXPIRED_GAUGE.labels(heuristic="gauge_exp")._value.get() == 1.0
        assert _HEURISTIC_EXPIRED_GAUGE.labels(heuristic="gauge_act")._value.get() == 0.0


class TestKnownHeuristicRegistrations:
    """Every shipped heuristic is registered with a complete, sunset-bounded
    provenance record."""

    _KNOWN = [
        "source_selection_bypass",
        "_ARABIC_FLAT_PREFER_MULTIPLIER",
        "force_verdict_override",
        "_try_image_enrichment",
        "_try_structural_pass",
        "_try_ocr_promotion",
        "_try_flat_promotion",
        "_try_content_class_promotion",
        "_try_small_doc_promotion",
    ]

    def test_all_known_heuristics_registered_with_bounded_provenance(self):
        problems = []
        for name in self._KNOWN:
            entry = registry.get(name)
            if entry is None:
                problems.append(f"  [{name}] not registered")
                continue
            if not entry.rfc_origin.startswith("RFC-"):
                problems.append(f"  [{name}] rfc_origin={entry.rfc_origin!r} lacks 'RFC-' prefix")
            if entry.created is None:
                problems.append(f"  [{name}] created is None")
            if not isinstance(entry.expiry, date):
                problems.append(f"  [{name}] expiry is not a date: {entry.expiry!r}")
            elif entry.created is not None and entry.expiry > entry.created + timedelta(days=91):
                problems.append(
                    f"  [{name}] expiry {entry.expiry} is more than 91 days "
                    f"after created {entry.created}"
                )
        assert not problems, "heuristic registration defects:\n" + "\n".join(problems)


# ===========================================================================
# preprocess_client  (RFC-015 D1 task 1.1/1.5 + RFC-046 D12 follow-up)
# ===========================================================================


def test_preprocess_supported_is_the_canonical_client_set():
    """preprocess_client.SUPPORTED is the same object as
    pageindex_mcp.client._SUPPORTED -- no local duplicate definition.

    Prior to this change preprocess_client.py hardcoded its own
    ``{".pdf", ".docx", ".pptx", ".md", ".txt", ".html"}``, silently excluding
    extensions the HTTP upload path already supports (``.jpg``, ``.xlsx``,
    ``.png``, ...); batch preprocessing of doc_store/ dropped those files with
    no warning (corpus audit 2026-07-17).
    """
    import preprocess_client

    assert preprocess_client.SUPPORTED is _SUPPORTED

    missing = [e for e in (".jpg", ".xlsx", ".png") if e not in preprocess_client.SUPPORTED]
    assert not missing, (
        f"{missing} missing from preprocess_client.SUPPORTED — batch "
        "preprocessing would silently skip these files"
    )

    old_hardcoded = {".pdf", ".docx", ".pptx", ".md", ".txt", ".html"}
    assert old_hardcoded <= preprocess_client.SUPPORTED


@pytest.mark.asyncio
async def test_jpg_file_enqueues_via_process_one(tmp_path):
    """Integration-style: a .jpg file in doc_store/ is accepted by
    _files_to_process and drives a converter-subprocess enqueue via
    _process_one — it is not silently skipped."""
    import preprocess_client

    jpg = tmp_path / "photo.jpg"
    jpg.write_bytes(b"\xff\xd8\xff\xe0fakejpegdata")

    # _files_to_process filters against SUPPORTED — confirm .jpg passes.
    with patch("preprocess_client.DOC_STORE", tmp_path):
        files = preprocess_client._files_to_process(None)
    assert jpg in files

    # Stub the converter-subprocess call (the actual "enqueue") so this stays
    # fast/offline, then confirm _process_one drives it for the .jpg file.
    fake_run = AsyncMock(return_value={"doc_id": "fake-doc-id", "content_class": "image"})
    sem = asyncio.Semaphore(1)

    with patch("pageindex_mcp.worker._run_converter_subprocess", fake_run, create=True):
        await preprocess_client._process_one(sem, jpg, "run-test")

    fake_run.assert_awaited_once_with(str(jpg))


def test_filtered_stderr_keeps_structured_records_drops_raw_tracebacks():
    """RFC-046 D12 follow-up: obs.configure() binds its handler to whatever
    sys.stderr is at the time, and on the batch route that is _FilteredStderr.
    A record whose msg merely *mentions* a noise trigger was silently dropped,
    leaving holes in the very log stream gate 12.C-core reconstructs from.
    Raw interpreter tracebacks must still be filtered out."""
    import io
    import json as _json

    from preprocess_client import _FilteredStderr

    sink = io.StringIO()
    record = _json.dumps({"v": 1, "msg": "litellm_logging.py emitted a warning"})
    _FilteredStderr(sink).write(record + "\n")
    assert sink.getvalue().strip() == record

    noise_sink = io.StringIO()
    _FilteredStderr(noise_sink).write("Task exception was never retrieved\n  File 'x.py', line 1\n")
    assert noise_sink.getvalue() == ""
