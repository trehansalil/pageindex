"""RFC-030 promotion tests — consolidated from test_rfc030_d0_d1.py,
test_rfc030_d2_d3.py, and test_rfc030_d4_d5.py.

Covers Properties 1-9 of RFC-030:
  1. Paired fence blocks (```...```) preserve enclosed content as prose
     blocks instead of being silently dropped by the old in_fence parity
     toggle.
  2. An odd/unclosed fence marker preserves all content after the stray
     marker instead of permanently discarding the rest of the document.
  3. A zero-block extraction from non-empty markdown triggers the
     LowQualityTreeError escalation path in client.index() instead of
     silently persisting an empty flat.json.
  4. _repeating_token_density returns None (not 0.0) below the 20-alnum-token
     floor, so "too short to assess" is distinguishable from "assessed and
     found clean".
  5. When _pre_density is None, retry_wins short-circuits to True regardless
     of _post_density, gated only by the absolute LOW_CONTENT_OCR_CHAR_FLOOR.
  6. When retry_wins is False, all six retry-derived state variables (result,
     ok, reason, md_content, tmp_md_path, pic_results) are reverted together
     to their pre-retry snapshots -- no partial revert.
  7. low_content_density threshold lowered to 150 chars/node.
  8. Unhandled validate_tree failure reasons persist as FAIL (via
     classify_verdict), not raised as LowQualityTreeError.
  9. _garble_check_nodes inspects node.get('title') in addition to
     node.get('text'), including RTL-reversed-morphology detection.
 10. _flatten_tree_text includes title text for every node.
 11. Bidi coherence wired into validate_tree (inline via decide_rtl).

Properties 4-6 mirror client.py's OCR retry guardrail block (~lines
1083-1171) the same way test_rfc028_d4.py mirrors the keep-best block --
the real logic lives in a closure nested inside CustomPageIndexClient.index()
and is not independently importable.
"""

import os
import tempfile
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from pageindex_mcp.client import CustomPageIndexClient
from pageindex_mcp.client import images as _img
from pageindex_mcp.client import indexer as _idx
from pageindex_mcp.helpers import (
    TreeDefect,
    TreeGateResult,
    _garble_check_nodes,
    _word_has_reversed_morphology,
    validate_tree,
)
from tests.conftest import filler_text


# ---------------------------------------------------------------------------
# Shared fixtures / harness
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
    """A real on-disk markdown file so index() runs up to (and past) the
    validate_tree branch."""
    fd, path = tempfile.mkstemp(suffix=".md")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("Just some flat prose with no headings whatsoever.\n")
    yield path
    if os.path.exists(path):
        os.unlink(path)


def _make_client():
    return CustomPageIndexClient(api_key="test-key")


async def _tree_coro(structure):
    return {"structure": structure, "doc_description": ""}


def _wire_common(monkeypatch, *, flat_doc_routing, validate_return, flat_return):
    """Patch every collaborator client.index() touches for the zero-block
    escalation tests, where the caller supplies the flat-extraction result."""
    monkeypatch.setattr(_idx, "settings", _fake_settings(flat_doc_routing))
    monkeypatch.setattr(_img, "settings", _fake_settings(flat_doc_routing))
    monkeypatch.setattr(_idx, "hash_cache_get", lambda filename: None)
    monkeypatch.setattr(_idx, "list_processed_docs", lambda: [])
    monkeypatch.setattr(_idx, "hash_cache_set", MagicMock())
    monkeypatch.setattr(_idx, "validate_tree", lambda structure, **kw: validate_return)

    idx_mocks = {
        "save_flat_doc": MagicMock(),
        "save_doc": MagicMock(),
        "save_raw": MagicMock(),
        "save_doc_meta": MagicMock(),
        "FLAT_DOCS_TOTAL": MagicMock(),
    }
    for name, m in idx_mocks.items():
        monkeypatch.setattr(_idx, name, m)

    img_mocks = {
        "route_and_extract_flat": MagicMock(return_value=flat_return),
        "LOW_QUALITY_TREES": MagicMock(),
    }
    for name, m in img_mocks.items():
        monkeypatch.setattr(_img, name, m)

    mocks = {**idx_mocks, **img_mocks}
    return mocks


def _wire_index(monkeypatch, *, validate_return, flat_doc_routing: bool = True):
    """Patch every collaborator client.index() touches for the
    persist-with-FAIL routing tests, where flat extraction always returns a
    fixed non-empty block."""
    monkeypatch.setattr(_idx, "settings", _fake_settings(flat_doc_routing))
    monkeypatch.setattr(_img, "settings", _fake_settings(flat_doc_routing))
    monkeypatch.setattr(_idx, "hash_cache_get", lambda filename: None)
    monkeypatch.setattr(_idx, "list_processed_docs", lambda: [])
    monkeypatch.setattr(_idx, "hash_cache_set", MagicMock())
    monkeypatch.setattr(_idx, "validate_tree", lambda structure, **kw: validate_return)

    idx_mocks = {
        "save_flat_doc": MagicMock(),
        "save_doc": MagicMock(),
        "save_raw": MagicMock(),
        "save_doc_meta": MagicMock(),
        "FLAT_DOCS_TOTAL": MagicMock(),
    }
    for name, m in idx_mocks.items():
        monkeypatch.setattr(_idx, name, m)

    img_mocks = {
        "route_and_extract_flat": MagicMock(
            return_value=("flat_prose", [{"role": "prose", "text": "x"}])
        ),
        "LOW_QUALITY_TREES": MagicMock(),
    }
    for name, m in img_mocks.items():
        monkeypatch.setattr(_img, name, m)

    mocks = {**idx_mocks, **img_mocks}
    return mocks


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


# ===========================================================================
# route_and_extract_flat: fence-block handling (Properties 1 & 2)
# ===========================================================================
# ===========================================================================
# CustomPageIndexClient.index(): zero-block escalation (Property 3)
# ===========================================================================
# ===========================================================================
# _repeating_token_density (mirrored closure): None below 20-token floor
# (Property 4)
# ===========================================================================


# Mirrors client.py's nested _repeating_token_density (~lines 1083-1098). The
# real function is a closure defined inside CustomPageIndexClient.index() and
# is not independently importable -- see test_rfc028_d4.py's _keep_best for
# the same mirroring pattern used against that method's other closures.
def _repeating_token_density(text: str) -> float | None:
    from collections import Counter

    tokens = [t for t in text.split() if any(c.isalnum() for c in t)]
    if len(tokens) < 20:
        return None
    return Counter(tokens).most_common(1)[0][1] / len(tokens)


class TestRepeatingTokenDensityNoneFloor:
    def test_empty_text_returns_none(self):
        assert _repeating_token_density("") is None

    def test_nineteen_tokens_returns_none(self):
        text = " ".join(f"tok{i}" for i in range(19))
        assert _repeating_token_density(text) is None


# ===========================================================================
# retry_wins short-circuit when _pre_density is None (Property 5)
# ===========================================================================


# Mirrors client.py's decision block at ~lines 1131-1153.
def _retry_wins_when_pre_density_none(
    post_retry_chars: int, char_floor: int = _idx.LOW_CONTENT_OCR_CHAR_FLOOR
) -> bool:
    return post_retry_chars >= char_floor


class TestRetryWinsShortCircuitOnNonePreDensity:
    def test_pre_density_none_post_above_floor_retry_wins(self):
        floor = _idx.LOW_CONTENT_OCR_CHAR_FLOOR
        assert _retry_wins_when_pre_density_none(floor + 1) is True

    def test_pre_density_none_post_below_floor_retry_loses(self):
        floor = _idx.LOW_CONTENT_OCR_CHAR_FLOOR
        assert _retry_wins_when_pre_density_none(floor - 1) is False


# ===========================================================================
# Atomic revert of all six retry-derived state variables (Property 6)
# ===========================================================================

# Mirrors the snapshot/revert shape that client.py's OCR retry block must
# maintain per RFC-030 D1: `result`, `ok`, `reason`, `md_content`,
# `tmp_md_path`, `pic_results` are captured together before the retry attempt
# and, on a losing retry, restored together -- so no field can be left
# pointing at post-retry data while its siblings point at pre-retry data.
_RETRY_STATE_FIELDS = ("result", "ok", "reason", "md_content", "tmp_md_path", "pic_results")


def _snapshot_and_maybe_revert(pre_state: dict, post_state: dict, retry_wins: bool) -> dict:
    if retry_wins:
        return dict(post_state)
    return dict(pre_state)


def _pre_state() -> dict:
    return {
        "result": {"structure": [{"title": "pre", "text": "pre-retry tree"}]},
        "ok": False,
        "reason": "node_count<3",
        "md_content": "pre-retry markdown",
        "tmp_md_path": "/tmp/pre.md",
        "pic_results": [{"index": 0, "ocr_text": "pre pic"}],
    }


def _post_state() -> dict:
    return {
        "result": {"structure": [{"title": "post", "text": "post-retry tree"}]},
        "ok": True,
        "reason": None,
        "md_content": "post-retry markdown",
        "tmp_md_path": "/tmp/post.md",
        "pic_results": [{"index": 0, "ocr_text": "post pic"}],
    }


class TestAtomicRevertOfAllSixStateVariables:
    def test_retry_loses_all_six_fields_revert_to_pre_retry_snapshot(self):
        pre, post = _pre_state(), _post_state()

        final = _snapshot_and_maybe_revert(pre, post, retry_wins=False)

        for field in _RETRY_STATE_FIELDS:
            assert final[field] == pre[field], (
                f"field {field!r} did not revert to pre-retry snapshot: "
                f"got {final[field]!r}, expected {pre[field]!r}"
            )
            assert final[field] != post[field], (
                f"field {field!r} leaked its post-retry value after a losing retry"
            )

    def test_retry_wins_all_six_fields_take_post_retry_value(self):
        pre, post = _pre_state(), _post_state()

        final = _snapshot_and_maybe_revert(pre, post, retry_wins=True)

        for field in _RETRY_STATE_FIELDS:
            assert final[field] == post[field]


# ===========================================================================
# validate_tree: low_content_density threshold lowered to 150 (Property 7)
# ===========================================================================
def _make_leaf(title: str, text: str) -> dict:
    """Return a leaf node (no children)."""
    return {"title": title, "text": text}


def _make_branch(title: str, text: str, children: list[dict]) -> dict:
    """Return an internal node with the given children."""
    return {"title": title, "text": text, "nodes": children}


def _density_tree(n_nodes: int, chars_per_node: int) -> list[dict]:
    """Build a tree with *n_nodes* total non-root nodes, each carrying
    *chars_per_node* chars. Mirrors the fixture pattern from
    test_rfc029_d1.py."""
    leaves = [_make_leaf(f"L{i}", filler_text(chars_per_node, i)) for i in range(n_nodes - 1)]
    branch = _make_branch("Section1", filler_text(chars_per_node, n_nodes), leaves)
    return [{"title": "Root", "text": filler_text(chars_per_node, n_nodes + 1), "nodes": [branch]}]


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


# ===========================================================================
# CustomPageIndexClient.index(): unhandled validate_tree reasons persist as
# FAIL, not raised as LowQualityTreeError (Property 6/8, client.py::index())
# ===========================================================================
#
# Mirrors the no-infra mocking harness from tests/test_client_contract.py:
# a real on-disk .md file drives index() up to the post-validate_tree
# branch; validate_tree's return value is stubbed at the branch, and every
# persistence collaborator (save_doc / save_flat_doc / save_raw /
# save_doc_meta / route_and_extract_flat) is mocked. classify_verdict is
# NOT mocked -- it runs for real against the structure supplied via
# _run_md_to_tree, so its verdict reflects actual production wiring.
_UNHANDLED_GATE_RESULTS = [
    TreeGateResult(
        ok=False,
        defect=TreeDefect.LOW_CONTENT_DENSITY,
        detail="chars_per_node=54.3,threshold=150.0",
    ),
    TreeGateResult(ok=False, defect=TreeDefect.SUSPECT_DENSITY, detail="chars_per_page=1200.0"),
    TreeGateResult(
        ok=False,
        defect=TreeDefect.EMPTY_NODE_CONTAMINATION,
        detail="fraction=0.62,empty_leaf=5,empty_non_leaf=3,total_non_root=13",
    ),
]


@pytest.mark.parametrize("reason", _UNHANDLED_GATE_RESULTS, ids=lambda gr: str(gr))
class TestPersistWithFailRouting:
    async def test_persists_via_save_doc_no_raise(self, monkeypatch, md_file, reason):
        """The unhandled reasons must persist via save_doc, not raise
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
        not PASS/MARGINAL -- even though the structure alone would score
        PASS."""
        structure = _pass_shaped_structure()
        mocks = _wire_index(monkeypatch, validate_return=reason)
        c = _make_client()
        monkeypatch.setattr(c, "_run_md_to_tree", lambda *a, **k: _tree_coro(structure))

        await c.index(md_file)

        meta_args = mocks["save_doc_meta"].call_args.args
        meta_dict = meta_args[1]
        assert meta_dict["verdict"] == "FAIL", (
            f"Expected FAIL verdict for unhandled reason {reason!r}, "
            f"got verdict={meta_dict['verdict']!r} reason={meta_dict.get('verdict_reason')!r}"
        )


class TestPassPathTreesUnaffected:
    """Regression: existing PASS-path trees (validate_tree ok=True) must still
    route through the normal tree path, unaffected by the persist-with-FAIL
    branch added for unhandled failure reasons."""

    async def test_pass_tree_persists_via_save_doc(self, monkeypatch, md_file):
        structure = _pass_shaped_structure()
        mocks = _wire_index(
            monkeypatch, validate_return=TreeGateResult(ok=True, defect=TreeDefect.OK)
        )
        c = _make_client()
        monkeypatch.setattr(c, "_run_md_to_tree", lambda *a, **k: _tree_coro(structure))

        doc_id = await c.index(md_file)

        assert isinstance(doc_id, str) and len(doc_id) == 36
        mocks["save_doc"].assert_called_once()
        mocks["route_and_extract_flat"].assert_not_called()
        mocks["LOW_QUALITY_TREES"].labels.assert_not_called()

    async def test_pass_tree_classify_verdict_still_pass(self, monkeypatch, md_file):
        structure = _pass_shaped_structure()
        mocks = _wire_index(
            monkeypatch, validate_return=TreeGateResult(ok=True, defect=TreeDefect.OK)
        )
        c = _make_client()
        monkeypatch.setattr(c, "_run_md_to_tree", lambda *a, **k: _tree_coro(structure))

        await c.index(md_file)

        meta_args = mocks["save_doc_meta"].call_args.args
        meta_dict = meta_args[1]
        assert meta_dict["verdict"] == "PASS"


# ===========================================================================
# _garble_check_nodes: title inspection incl. RTL-reversed morphology
# (Property 9)
# ===========================================================================

# RFC-034 D7: presentation-form glyphs decompose to base Arabic under NFKC
# before these detectors run, so the morphological reversal fixture is now a
# character-reversed base-Arabic word (mirrors test_rfc028_d3.py) rather than
# a raw presentation-form glyph.
_REVERSED_TITLE_WORD = "رارق"  # "قرار" (decision) reversed at the character level


def _title_leaf(title: str, text: str) -> dict:
    return {"title": title, "text": text, "nodes": []}


class TestGarbledTitleWithCleanTextDetected:
    def test_garbled_title_clean_text_counts_as_garbled_node(self):
        node = _title_leaf(title="��� corrupted title", text="This is clean prose.")

        garbled = _garble_check_nodes([node])

        assert garbled == 1

    def test_clean_title_clean_text_not_garbled(self):
        node = _title_leaf(title="Section One", text="This is clean prose.")

        garbled = _garble_check_nodes([node])

        assert garbled == 0


class TestRTLReversedTitleDetected:
    def test_word_has_reversed_morphology_flags_final_form_at_start(self):
        assert _word_has_reversed_morphology(_REVERSED_TITLE_WORD) is True

    def test_reversed_arabic_title_detected_via_garble_check_nodes(self):
        node = _title_leaf(title=_REVERSED_TITLE_WORD, text="clean body text")

        garbled = _garble_check_nodes([node])

        assert garbled == 1


# ===========================================================================
# _flatten_tree_text: title text included for every node (Property 10)
# ===========================================================================
# ===========================================================================
# validate_tree: bidi coherence wired in via decide_rtl (Property 11)
# ===========================================================================
def _healthy_leaf(title: str, text: str) -> dict:
    return {"title": title, "text": text, "nodes": []}


def _visual_order_tree() -> list:
    """An Arabic-dominant tree with visual-order (reversed) content.
    Zone-3 unified decide_rtl needs >=15% Arabic ratio to evaluate,
    so the tree must be Arabic-dominant for the bidi coherence gate
    to fire. Uses varied real Arabic words (not repeated) to avoid
    triggering the token_repetition garble prong."""
    lines = [
        "ةيبرعلا ةغللا ملعت يف ةمدقم",
        "ةيساسألا دعاوقلا حرش ىلإ فدهي",
        "ةحيحصلا ةقيرطلاب ةباتكلا",
        "ةيوغللا تاراهملا ريوطت",
        "يبرعلا بدألا خيرات ةسارد",
    ]
    arabic_body = "\n".join(lines)
    return [
        {
            "title": "Root",
            "text": arabic_body,
            "nodes": [
                _healthy_leaf("لوألا لصفلا", arabic_body),
                _healthy_leaf("يناثلا لصفلا", arabic_body),
                _healthy_leaf("ثلاثلا لصفلا", arabic_body),
            ],
        }
    ]


# ===========================================================================
# classify_verdict: bidi_degraded caps at MARGINAL, never upgrades a FAIL
# ===========================================================================
def _varied_text(seed: int) -> str:
    """Non-repeating filler that avoids the garble/token-repetition heuristics
    (mirrors test_verdict_rfc015.py's fixture helper)."""
    return " ".join(f"word{seed}n{j}alpha" for j in range(60))


def _passing_tree():
    """A well-formed tree with evenly-sized leaves (low leaf-concentration
    ratio) that classify_verdict grades PASS, used to prove bidi_degraded
    caps the verdict rather than upgrading it."""
    return [
        {
            "title": "Chapter",
            "text": "",
            "nodes": [_healthy_leaf(f"Leaf {i}", _varied_text(i)) for i in range(5)],
        }
    ]
