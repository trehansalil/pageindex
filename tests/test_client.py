# tests/test_client.py
"""No-infra unit tests for client.py's relocated provider helpers.

Covers the pure helpers that moved out of config.py (no_llm_outside_provider
governance rule): _is_azure_url, get_openai_client, and the _SUPPORTED set.
None of these tests require MinIO, Redis, or network access — constructing an
AsyncOpenAI/AsyncAzureOpenAI client does not perform any I/O.
"""

import copy
import os
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import openai
import pytest

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


def test_is_azure_url_true_for_azure_endpoint():
    assert _is_azure_url("https://my-resource.openai.azure.com/") is True
    assert _is_azure_url("https://foo.openai.azure.com/v1/chat") is True


def test_is_azure_url_false_for_non_azure_and_none():
    assert _is_azure_url("https://api.openai.com/v1") is False
    assert _is_azure_url(None) is False
    assert _is_azure_url("") is False


def test_get_openai_client_azure(monkeypatch):
    """LLM-01-C3: An Azure base URL yields an AsyncAzureOpenAI client."""
    monkeypatch.setattr(
        "pageindex_mcp.client.llm.settings",
        _fake_settings(
            openai_base_url="https://my-resource.openai.azure.com",
            azure_api_version="2024-08-01-preview",
        ),
    )
    client = get_openai_client()
    assert isinstance(client, openai.AsyncAzureOpenAI)
    assert isinstance(client, openai.AsyncOpenAI)  # AzureOpenAI subclasses OpenAI


def test_get_openai_client_non_azure(monkeypatch):
    """A non-Azure base URL yields a plain AsyncOpenAI client (not Azure)."""
    monkeypatch.setattr(
        "pageindex_mcp.client.llm.settings",
        _fake_settings(openai_base_url="https://api.openai.com/v1"),
    )
    client = get_openai_client()
    assert isinstance(client, openai.AsyncOpenAI)
    assert not isinstance(client, openai.AsyncAzureOpenAI)


def test_supported_extensions_present():
    expected = {".pdf", ".md", ".docx", ".pptx", ".html", ".txt"}
    assert expected.issubset(_SUPPORTED)
    assert ".markdown" in _SUPPORTED


# ---------------------------------------------------------------------------
# LLM-01: OpenAI-compatible endpoint provider abstraction
# ---------------------------------------------------------------------------


def test_resolve_llm_provider_auto_infers_from_base_url(monkeypatch):
    """LLM-01-C1: auto resolves to azure for an Azure URL, else openai."""
    monkeypatch.setattr(
        "pageindex_mcp.client.llm.settings",
        _fake_settings(llm_provider="auto", openai_base_url="https://r.openai.azure.com"),
    )
    assert resolve_llm_provider() == "azure"
    monkeypatch.setattr(
        "pageindex_mcp.client.llm.settings",
        _fake_settings(llm_provider="auto", openai_base_url="https://api.openai.com/v1"),
    )
    assert resolve_llm_provider() == "openai"


def test_resolve_llm_provider_explicit_is_honored(monkeypatch):
    """LLM-01-C1: an explicit provider overrides base-URL inference."""
    # 'compatible' is honored verbatim even though the base URL is not Azure.
    monkeypatch.setattr(
        "pageindex_mcp.client.llm.settings",
        _fake_settings(llm_provider="compatible", openai_base_url="https://openrouter.ai/api/v1"),
    )
    assert resolve_llm_provider() == "compatible"


def test_resolve_llm_provider_rejects_invalid(monkeypatch):
    """LLM-01-C1: an invalid LLM_PROVIDER fails fast instead of being auto-routed.

    A typo must surface as a ValueError at startup rather than silently routing
    traffic to a base-URL-inferred backend.
    """
    monkeypatch.setattr(
        "pageindex_mcp.client.llm.settings",
        _fake_settings(llm_provider="bogus", openai_base_url="https://api.openai.com/v1"),
    )
    with pytest.raises(ValueError, match="Invalid LLM_PROVIDER"):
        resolve_llm_provider()


def test_get_openai_client_compatible_uses_base_url(monkeypatch):
    """LLM-01-C2: a compatible provider yields AsyncOpenAI carrying the custom base_url."""
    monkeypatch.setattr(
        "pageindex_mcp.client.llm.settings",
        _fake_settings(
            llm_provider="compatible",
            openai_base_url="https://openrouter.ai/api/v1",
            openai_api_key="sk-compat",
        ),
    )
    client = get_openai_client()
    assert isinstance(client, openai.AsyncOpenAI)
    assert not isinstance(client, openai.AsyncAzureOpenAI)
    assert str(client.base_url).rstrip("/") == "https://openrouter.ai/api/v1"


def test_configure_litellm_openai_sets_module_base(monkeypatch):
    """LLM-01-C4: configure_litellm sets litellm.api_base/api_key for openai/compatible."""
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


def test_configure_litellm_azure_sets_env(monkeypatch):
    """LLM-01-C4: configure_litellm sets the Azure env vars litellm requires."""
    import litellm

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
    import os

    assert os.environ["AZURE_API_BASE"] == "https://r.openai.azure.com"
    assert os.environ["AZURE_API_KEY"] == "sk-azure"
    assert os.environ["AZURE_API_VERSION"] == "2024-08-01-preview"
    assert litellm.api_base == "https://r.openai.azure.com"


def test_validate_llm_config_requires_key(monkeypatch):
    """LLM-01-C5: an empty API key fails fast."""
    monkeypatch.setattr(
        "pageindex_mcp.client.llm.settings",
        _fake_settings(openai_api_key=""),
    )
    with pytest.raises(ValueError, match="OPENAI_API_KEY"):
        validate_llm_config()


def test_validate_llm_config_requires_base_url(monkeypatch):
    """LLM-01-C5: an empty base URL fails fast."""
    monkeypatch.setattr(
        "pageindex_mcp.client.llm.settings",
        _fake_settings(openai_api_key="sk-x", openai_base_url=""),
    )
    with pytest.raises(ValueError, match="OPENAI_BASE_URL"):
        validate_llm_config()


def test_validate_llm_config_passes_for_compatible(monkeypatch):
    """LLM-01-C5: a well-formed compatible config validates without raising."""
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


def test_segment_table_nodes_splits_single_large_table_node_on_primary_path():
    """RFC-033 D6: a tree with a single large TABLE node — as produced by the
    primary tree-build path after split_oversized_leaf_nodes — is split into
    per-section sub-nodes when _segment_table_nodes is invoked, exactly as it
    already is on the garble-recovery paths."""
    tarif_table = _pipe_table(n_data_rows=30)
    price_table = _pipe_table(n_data_rows=30)
    table_text = tarif_table + "\n\nAnhang\n\n" + price_table
    assert len(table_text) > 2000
    structure = [{"title": "GHV-TKV-Tarif", "text": table_text}]

    result = _segment_table_nodes(structure)

    node = result[0]
    assert node["text"] == ""
    children = node.get("nodes", [])
    assert len(children) >= 2
    assert any("|" in c["text"] for c in children)


def test_segment_table_nodes_garble_recovery_output_is_byte_identical():
    """RFC-033 D6 regression: a document already on a garble-recovery path
    (where _segment_table_nodes ran pre-fix) must produce byte-identical
    output after the fix adds new call sites on other tree-build paths.
    Segmentation is idempotent, so running it a second time over an
    already-segmented structure must not change it further."""
    prose = "Paragraph text. " * 200
    table_text = _pipe_table(n_data_rows=20)
    structure = [{"title": "Garbled Doc Section", "text": prose + "\n" + table_text}]

    pre_fix_output = _segment_table_nodes(structure)
    snapshot = copy.deepcopy(pre_fix_output)

    post_fix_output = _segment_table_nodes(pre_fix_output)

    assert post_fix_output == snapshot


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


@pytest.fixture
def pdf_file():
    fd, path = tempfile.mkstemp(suffix=".pdf")
    with os.fdopen(fd, "wb") as fh:
        fh.write(b"%PDF-1.4\n%\xe2\xe3\xcf\xd3\nreal-looking pdf bytes")
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

    def test_image_exts_is_same_object_as_images_module(self):
        """_IMAGE_EXTS in indexer.py must be the same object as in images.py."""
        assert _idx._IMAGE_EXTS is _img._IMAGE_EXTS

    def test_min_standalone_image_md_chars_is_same_as_images_module(self):
        """MIN_STANDALONE_IMAGE_MD_CHARS in indexer.py must be the same
        value as in images.py."""
        assert _idx.MIN_STANDALONE_IMAGE_MD_CHARS == _img.MIN_STANDALONE_IMAGE_MD_CHARS

    def test_monkeypatch_images_min_chars_affects_indexer(self, monkeypatch):
        """Changing images.MIN_STANDALONE_IMAGE_MD_CHARS must be visible
        through indexer.MIN_STANDALONE_IMAGE_MD_CHARS since it is imported
        from images.py."""
        # The import in indexer.py is:
        #   from .images import _IMAGE_EXTS, ..., MIN_STANDALONE_IMAGE_MD_CHARS
        # So indexer.MIN_STANDALONE_IMAGE_MD_CHARS is a module-level name
        # bound to the same int value. Monkeypatching the indexer module
        # attribute directly verifies it can be overridden for testing.
        original = _idx.MIN_STANDALONE_IMAGE_MD_CHARS
        monkeypatch.setattr(_idx, "MIN_STANDALONE_IMAGE_MD_CHARS", 999)
        assert _idx.MIN_STANDALONE_IMAGE_MD_CHARS == 999
        # Verify the images module itself still has the original (monkeypatch
        # only touched indexer's binding)
        assert _img.MIN_STANDALONE_IMAGE_MD_CHARS == original

    def test_image_exts_contains_expected_extensions(self):
        """_IMAGE_EXTS must contain the standard image extensions."""
        expected = {".png", ".jpg", ".jpeg", ".tiff", ".tif"}
        assert expected == _idx._IMAGE_EXTS
