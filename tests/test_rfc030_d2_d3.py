"""RFC-030 D2/D3 tests — Tasks 1.2, 1.4.

Covers:
  1. Property 7: low_content_density threshold lowered to 150 chars/node.
  2. Property 6: unhandled validate_tree failure reasons persist as FAIL
     (via classify_verdict), not raised as LowQualityTreeError.
"""

import os
import tempfile
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import pageindex_mcp.client as client_mod
from pageindex_mcp.client import CustomPageIndexClient
from pageindex_mcp.helpers import _RFC029_MIN_CHARS_PER_NODE, LowQualityTreeError, TreeDefect, TreeGateResult, validate_tree
from tests.conftest import filler_text


def _make_leaf(title: str, text: str) -> dict:
    """Return a leaf node (no children)."""
    return {"title": title, "text": text}


def _make_branch(title: str, text: str, children: list[dict]) -> dict:
    """Return an internal node with the given children."""
    return {"title": title, "text": text, "nodes": children}


def _density_tree(n_nodes: int, chars_per_node: int) -> list[dict]:
    """Build a tree with *n_nodes* total non-root nodes, each carrying
    *chars_per_node* chars. Mirrors the fixture pattern from test_rfc029_d1.py."""
    leaves = [_make_leaf(f"L{i}", filler_text(chars_per_node, i)) for i in range(n_nodes - 1)]
    branch = _make_branch("Section1", filler_text(chars_per_node, n_nodes), leaves)
    return [
        {"title": "Root", "text": filler_text(chars_per_node, n_nodes + 1), "nodes": [branch]}
    ]


class TestDensityThresholdBoundary:
    def test_300_nodes_300_chars_passes(self):
        """300 nodes at 300 chars/node must pass low_content_density (was
        rejected at the old 500 threshold, passes at the new 150 threshold)."""
        tree = _density_tree(n_nodes=300, chars_per_node=300)

        ok, reason = validate_tree(tree)

        assert "low_content_density" not in reason
        assert ok is True

    def test_300_nodes_50_chars_still_fails(self):
        """300 nodes at 50 chars/node must still fail low_content_density
        even at the lowered 150 threshold."""
        tree = _density_tree(n_nodes=300, chars_per_node=50)

        ok, reason = validate_tree(tree)

        assert ok is False
        assert reason.startswith("low_content_density")

    def test_200_nodes_160_chars_passes(self):
        """200 nodes at 160 chars/node must pass — above the new 150
        threshold (would have failed under the old 500 threshold)."""
        tree = _density_tree(n_nodes=200, chars_per_node=160)

        ok, reason = validate_tree(tree)

        assert "low_content_density" not in reason
        assert ok is True

    def test_threshold_constant_is_150(self):
        """_RFC029_MIN_CHARS_PER_NODE must be 150.0 per RFC-030 D3."""
        assert _RFC029_MIN_CHARS_PER_NODE == 150.0


# ---------------------------------------------------------------------------
# Property 6: unhandled validate_tree reasons persist as FAIL, not ERROR
# (RFC-030 D2, client.py::index()) — Task 1.4.
#
# Mirrors the no-infra mocking harness from tests/test_client_contract.py:
# a real on-disk .md file drives index() up to the post-validate_tree
# branch; validate_tree's return value is stubbed at the branch, and every
# persistence collaborator (save_doc / save_flat_doc / save_raw /
# save_doc_meta / route_and_extract_flat) is mocked. classify_verdict is
# NOT mocked — it runs for real against the structure supplied via
# _run_md_to_tree, so its verdict reflects actual production wiring.
# ---------------------------------------------------------------------------


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


@pytest.fixture
def md_file():
    """A real on-disk markdown file so index() runs up to the validate_tree branch."""
    fd, path = tempfile.mkstemp(suffix=".md")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("Just some flat prose with no headings whatsoever.\n")
    yield path
    if os.path.exists(path):
        os.unlink(path)


def _pass_shaped_structure() -> list[dict]:
    """A well-formed depth-2 tree (node_count=8, max_leaf_ratio≈0.14, clean
    prose) that classify_verdict scores PASS on its own structural merits
    when validate_reason=None. Used so a FAIL assertion for an unhandled
    validate_tree reason genuinely exercises the reason→verdict wiring
    rather than being coincidentally FAIL from a degenerate structure."""
    words = "The quick brown fox jumps over the lazy dog near the river bank. "
    leaves = [{"title": f"Leaf {i}", "text": words * 20, "nodes": []} for i in range(7)]
    branch = {"title": "Section", "text": words * 20, "nodes": leaves}
    return [{"title": "Root", "text": "", "nodes": [branch]}]


def _wire_index(monkeypatch, *, validate_return: TreeGateResult | tuple, flat_doc_routing: bool = True):
    """Patch every collaborator client.index() touches; return the mocks dict."""
    monkeypatch.setattr(client_mod, "settings", _fake_settings(flat_doc_routing))
    monkeypatch.setattr(client_mod, "hash_cache_get", lambda filename: None)
    monkeypatch.setattr(client_mod, "list_processed_docs", lambda: [])
    monkeypatch.setattr(client_mod, "hash_cache_set", MagicMock())
    monkeypatch.setattr(client_mod, "validate_tree", lambda structure, **kw: validate_return)

    mocks = {
        "route_and_extract_flat": MagicMock(
            return_value=("flat_prose", [{"role": "prose", "text": "x"}])
        ),
        "save_flat_doc": MagicMock(),
        "save_doc": MagicMock(),
        "save_raw": MagicMock(),
        "save_doc_meta": MagicMock(),
        "write_verdict": MagicMock(),
        "FLAT_DOCS_TOTAL": MagicMock(),
        "LOW_QUALITY_TREES": MagicMock(),
    }
    for name, m in mocks.items():
        monkeypatch.setattr(client_mod, name, m)
    return mocks


def _make_client():
    return CustomPageIndexClient(api_key="test-key")


async def _tree_coro(structure):
    return {"structure": structure, "doc_description": ""}


_UNHANDLED_GATE_RESULTS = [
    TreeGateResult(ok=False, defect=TreeDefect.LOW_CONTENT_DENSITY, detail="chars_per_node=54.3,threshold=150.0"),
    TreeGateResult(ok=False, defect=TreeDefect.SUSPECT_DENSITY, detail="chars_per_page=1200.0"),
    TreeGateResult(ok=False, defect=TreeDefect.EMPTY_NODE_CONTAMINATION, detail="fraction=0.62,empty_leaf=5,empty_non_leaf=3,total_non_root=13"),
    TreeGateResult(ok=False, defect=TreeDefect.ARABIC_LOW_CONTENT_RATIO),
]


@pytest.mark.parametrize("reason", _UNHANDLED_GATE_RESULTS, ids=lambda gr: str(gr))
class TestPersistWithFailRouting:
    async def test_persists_via_save_doc_no_raise(self, monkeypatch, md_file, reason):
        """The four unhandled reasons must persist via save_doc, not raise
        LowQualityTreeError."""
        structure = _pass_shaped_structure()
        mocks = _wire_index(monkeypatch, validate_return=reason)
        c = _make_client()
        monkeypatch.setattr(c, "_run_md_to_tree", lambda *a, **k: _tree_coro(structure))

        doc_id = await c.index(md_file)

        assert isinstance(doc_id, str) and len(doc_id) == 36
        mocks["save_doc"].assert_called_once()

    async def test_classify_verdict_returns_fail(self, monkeypatch, md_file, reason):
        """classify_verdict must assign a FAIL verdict for the persisted tree,
        not PASS/MARGINAL — even though the structure alone would score PASS."""
        structure = _pass_shaped_structure()
        mocks = _wire_index(monkeypatch, validate_return=reason)
        c = _make_client()
        monkeypatch.setattr(c, "_run_md_to_tree", lambda *a, **k: _tree_coro(structure))

        await c.index(md_file)

        wv_args = mocks["write_verdict"].call_args.args
        assert wv_args[1] == "FAIL", (
            f"Expected FAIL verdict for unhandled reason {reason!r}, "
            f"got verdict={wv_args[1]!r} reason={wv_args[2]!r}"
        )

    async def test_no_flat_routing_no_ocr_retry(self, monkeypatch, md_file, reason):
        """No flat extraction and no flat persistence path for these reasons —
        the tree keeps its own artifact path (save_doc), not save_flat_doc."""
        structure = _pass_shaped_structure()
        mocks = _wire_index(monkeypatch, validate_return=reason)
        c = _make_client()
        monkeypatch.setattr(c, "_run_md_to_tree", lambda *a, **k: _tree_coro(structure))

        await c.index(md_file)

        mocks["route_and_extract_flat"].assert_not_called()
        mocks["save_flat_doc"].assert_not_called()
        mocks["FLAT_DOCS_TOTAL"].labels.assert_not_called()

    async def test_tree_structure_persisted_unchanged(self, monkeypatch, md_file, reason):
        """The structure passed to save_doc must be identical to the structure
        returned by the tree build — no flattening, no modification."""
        structure = _pass_shaped_structure()
        mocks = _wire_index(monkeypatch, validate_return=reason)
        c = _make_client()
        monkeypatch.setattr(c, "_run_md_to_tree", lambda *a, **k: _tree_coro(structure))

        await c.index(md_file)

        persisted_doc = mocks["save_doc"].call_args.args[1]
        assert persisted_doc["structure"] == structure

    async def test_low_quality_trees_metric_not_incremented(self, monkeypatch, md_file, reason):
        """The terminal-reject LOW_QUALITY_TREES counter belongs to the raise
        path only; unhandled reasons that persist must not increment it."""
        structure = _pass_shaped_structure()
        mocks = _wire_index(monkeypatch, validate_return=reason)
        c = _make_client()
        monkeypatch.setattr(c, "_run_md_to_tree", lambda *a, **k: _tree_coro(structure))

        await c.index(md_file)

        mocks["LOW_QUALITY_TREES"].labels.assert_not_called()


class TestPassPathTreesUnaffected:
    """Regression: existing PASS-path trees (validate_tree ok=True) must still
    route through the normal tree path, unaffected by the D2 persist-with-FAIL
    branch added for unhandled failure reasons."""

    async def test_pass_tree_persists_via_save_doc(self, monkeypatch, md_file):
        structure = _pass_shaped_structure()
        mocks = _wire_index(monkeypatch, validate_return=TreeGateResult(ok=True, defect=TreeDefect.OK))
        c = _make_client()
        monkeypatch.setattr(c, "_run_md_to_tree", lambda *a, **k: _tree_coro(structure))

        doc_id = await c.index(md_file)

        assert isinstance(doc_id, str) and len(doc_id) == 36
        mocks["save_doc"].assert_called_once()
        mocks["route_and_extract_flat"].assert_not_called()
        mocks["LOW_QUALITY_TREES"].labels.assert_not_called()

    async def test_pass_tree_classify_verdict_still_pass(self, monkeypatch, md_file):
        structure = _pass_shaped_structure()
        mocks = _wire_index(monkeypatch, validate_return=TreeGateResult(ok=True, defect=TreeDefect.OK))
        c = _make_client()
        monkeypatch.setattr(c, "_run_md_to_tree", lambda *a, **k: _tree_coro(structure))

        await c.index(md_file)

        wv_args = mocks["write_verdict"].call_args.args
        assert wv_args[1] == "PASS"

    @pytest.mark.parametrize("gate_result", [
        TreeGateResult(ok=False, defect=TreeDefect.NODE_COUNT_LOW),
        TreeGateResult(ok=False, defect=TreeDefect.DEPTH_LOW),
    ], ids=lambda gr: str(gr))
    async def test_handled_reasons_still_raise(self, monkeypatch, md_file, gate_result):
        """Sanity check: node_count/depth defects with flat_doc_routing=False
        still raise LowQualityTreeError (they route to REJECT and hit the
        terminal raise). Garbling no longer raises — zone-5 routes it through
        persist-with-FAIL via _flat_garble_unrecovered."""
        mocks = _wire_index(monkeypatch, validate_return=gate_result, flat_doc_routing=False)
        c = _make_client()
        monkeypatch.setattr(c, "_run_md_to_tree", lambda *a, **k: _tree_coro([]))

        with pytest.raises(LowQualityTreeError) as exc:
            await c.index(md_file)

        assert exc.value.reason == gate_result.defect.value
        mocks["save_doc"].assert_not_called()
