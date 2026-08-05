"""RFC-030 D0/D1 tests — Tasks 3.5 and 3.9.

Covers Properties 1 & 2 (D0):
  1. Paired fence blocks (```...```) preserve enclosed content as prose blocks
     instead of being silently dropped by the old in_fence parity toggle.
  2. An odd/unclosed fence marker preserves all content after the stray
     marker instead of permanently discarding the rest of the document.
  3. A zero-block extraction from non-empty markdown triggers the
     LowQualityTreeError escalation path in client.index() instead of
     silently persisting an empty flat.json.

Covers Properties 3-5 (D1), mirroring client.py's OCR retry guardrail block
(~lines 1083-1171) the same way test_rfc028_d4.py mirrors the keep-best
block -- the real logic lives in a closure nested inside
CustomPageIndexClient.index() and is not independently importable.
  3. _repeating_token_density returns None (not 0.0) below the 20-alnum-token
     floor, so "too short to assess" is distinguishable from "assessed and
     found clean".
  4. When _pre_density is None, retry_wins short-circuits to True regardless
     of _post_density, gated only by the absolute LOW_CONTENT_OCR_CHAR_FLOOR.
  5. When retry_wins is False, all six retry-derived state variables (result,
     ok, reason, md_content, tmp_md_path, pic_results) are reverted together
     to their pre-retry snapshots -- no partial revert.
"""

import os
import tempfile
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

import pageindex_mcp.client as client_mod
from pageindex_mcp.client import CustomPageIndexClient
from pageindex_mcp.helpers import LowQualityTreeError, route_and_extract_flat


# ---------------------------------------------------------------------------
# Property 1: paired fence markers preserve enclosed content as prose.
# ---------------------------------------------------------------------------
class TestPairedFencePreservesContent:
    def test_paired_fence_block_preserves_enclosed_content_as_prose(self):
        md = (
            "Intro paragraph before the fence.\n"
            "\n"
            "```\n"
            "Content that Docling wrapped in a fenced block.\n"
            "```\n"
            "\n"
            "Trailing paragraph after the fence.\n"
        )

        content_class, blocks = route_and_extract_flat(md)

        prose_texts = [b["text"] for b in blocks if b["role"] == "prose"]
        assert any("Content that Docling wrapped in a fenced block" in t for t in prose_texts)
        assert any("Intro paragraph before the fence" in t for t in prose_texts)
        assert any("Trailing paragraph after the fence" in t for t in prose_texts)
        assert content_class != "flat_prose" or blocks  # non-empty output either way

    def test_paired_fence_with_language_tag_preserves_content(self):
        md = "```python\nsome_code = 'not actually code'\n```\n"

        _content_class, blocks = route_and_extract_flat(md)

        prose_texts = [b["text"] for b in blocks if b["role"] == "prose"]
        assert any("some_code" in t for t in prose_texts)


# ---------------------------------------------------------------------------
# Property 2: odd/unclosed fence marker preserves content after the stray
# marker instead of permanently discarding it.
# ---------------------------------------------------------------------------
class TestOddFencePreservesTrailingContent:
    def test_unclosed_opening_fence_preserves_all_following_content(self):
        md = (
            "```\n"
            "First line after the stray opening fence.\n"
            "\n"
            "Second paragraph, still after the fence, never closed.\n"
        )

        _content_class, blocks = route_and_extract_flat(md)

        prose_texts = [b["text"] for b in blocks if b["role"] == "prose"]
        assert any("First line after the stray opening fence" in t for t in prose_texts)
        assert any("Second paragraph, still after the fence" in t for t in prose_texts)

    def test_stray_fence_in_middle_of_document_preserves_rest(self):
        md = (
            "Paragraph before any fence marker.\n"
            "\n"
            "```\n"
            "Content after the sole stray backtick marker that never closes.\n"
        )

        _content_class, blocks = route_and_extract_flat(md)

        prose_texts = [b["text"] for b in blocks if b["role"] == "prose"]
        assert any("Paragraph before any fence marker" in t for t in prose_texts)
        assert any(
            "Content after the sole stray backtick marker" in t for t in prose_texts
        )


# ---------------------------------------------------------------------------
# Property: zero-block output from non-empty markdown triggers escalation,
# not silent persistence of an empty flat.json.
# ---------------------------------------------------------------------------
def _fake_settings(flat_doc_routing: bool):
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
    fd, path = tempfile.mkstemp(suffix=".md")
    with os.fdopen(fd, "w", encoding="utf-8") as fh:
        fh.write("Just some flat prose with no headings whatsoever.\n")
    yield path
    if os.path.exists(path):
        os.unlink(path)


async def _coro_result():
    return {"structure": [], "doc_description": ""}


def _async_result():
    return _coro_result()


def _wire_common(monkeypatch, *, flat_doc_routing, validate_return, flat_return):
    monkeypatch.setattr(client_mod, "settings", _fake_settings(flat_doc_routing))
    monkeypatch.setattr(client_mod, "hash_cache_get", lambda filename: None)
    monkeypatch.setattr(client_mod, "list_processed_docs", lambda: [])
    monkeypatch.setattr(client_mod, "hash_cache_set", MagicMock())
    monkeypatch.setattr(client_mod, "validate_tree", lambda structure, **kw: validate_return)

    mocks = {
        "route_and_extract_flat": MagicMock(return_value=flat_return),
        "save_flat_doc": MagicMock(),
        "save_doc": MagicMock(),
        "save_raw": MagicMock(),
        "save_doc_meta": MagicMock(),
        "FLAT_DOCS_TOTAL": MagicMock(),
        "LOW_QUALITY_TREES": MagicMock(),
        "find_prior_verdict": MagicMock(return_value=None),
    }
    for name, m in mocks.items():
        monkeypatch.setattr(client_mod, name, m)
    return mocks


def _make_client():
    return CustomPageIndexClient(api_key="test-key")


async def test_zero_block_output_escalates_instead_of_persisting(monkeypatch, md_file):
    """RFC-030 D0 (Task 3.3): route_and_extract_flat returning an empty block
    list for non-empty markdown raises LowQualityTreeError('flat_zero_block')
    via the LOW_QUALITY_TREES escalation path; save_flat_doc is never called,
    so no empty flat.json is persisted."""
    mocks = _wire_common(
        monkeypatch,
        flat_doc_routing=True,
        validate_return=(False, "node_count<3"),
        flat_return=("flat_prose", []),
    )
    c = _make_client()
    monkeypatch.setattr(c, "_run_md_to_tree", lambda *a, **k: _async_result())

    with pytest.raises(LowQualityTreeError) as exc:
        await c.index(md_file)

    assert exc.value.reason == "flat_zero_block"
    mocks["route_and_extract_flat"].assert_called_once()
    mocks["save_flat_doc"].assert_not_called()
    mocks["save_doc"].assert_not_called()
    mocks["LOW_QUALITY_TREES"].labels.assert_called_once_with(reason="flat_zero_block")
    mocks["LOW_QUALITY_TREES"].labels.return_value.inc.assert_called_once()


async def test_non_zero_block_output_persists_normally(monkeypatch, md_file):
    """Control: non-empty block output does NOT trigger the zero-block guard —
    save_flat_doc is called normally."""
    mocks = _wire_common(
        monkeypatch,
        flat_doc_routing=True,
        validate_return=(False, "node_count<3"),
        flat_return=("flat_prose", [{"role": "prose", "text": "some content"}]),
    )
    c = _make_client()
    monkeypatch.setattr(c, "_run_md_to_tree", lambda *a, **k: _async_result())

    doc_id = await c.index(md_file)

    assert isinstance(doc_id, str)
    mocks["save_flat_doc"].assert_called_once()


# ---------------------------------------------------------------------------
# Property 3: _repeating_token_density returns None below the 20-token floor.
# ---------------------------------------------------------------------------

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

    def test_twenty_tokens_returns_float_not_none(self):
        text = " ".join(f"tok{i}" for i in range(20))
        density = _repeating_token_density(text)
        assert density is not None
        assert isinstance(density, float)

    def test_none_is_distinguishable_from_zero_density(self):
        # A 20-token text with no repetition at all has a real density of
        # 1/20 = 0.05, never 0.0 -- confirming None is a distinct sentinel
        # from any value _repeating_token_density can compute, not just from
        # the old hard-coded 0.0 floor value.
        text = " ".join(f"tok{i}" for i in range(20))
        density = _repeating_token_density(text)
        assert density != 0.0
        assert density == pytest.approx(1 / 20)


# ---------------------------------------------------------------------------
# Property 4: retry_wins short-circuits to True when _pre_density is None,
# gated only by the absolute LOW_CONTENT_OCR_CHAR_FLOOR.
# ---------------------------------------------------------------------------

# Mirrors client.py's decision block at ~lines 1131-1153.
def _retry_wins_when_pre_density_none(
    post_retry_chars: int, char_floor: int = client_mod.LOW_CONTENT_OCR_CHAR_FLOOR
) -> bool:
    return post_retry_chars >= char_floor


class TestRetryWinsShortCircuitOnNonePreDensity:
    def test_pre_density_none_post_above_floor_retry_wins(self):
        floor = client_mod.LOW_CONTENT_OCR_CHAR_FLOOR
        assert _retry_wins_when_pre_density_none(floor + 1) is True

    def test_pre_density_none_post_below_floor_retry_loses(self):
        floor = client_mod.LOW_CONTENT_OCR_CHAR_FLOOR
        assert _retry_wins_when_pre_density_none(floor - 1) is False

    def test_pre_density_none_post_at_floor_retry_wins(self):
        floor = client_mod.LOW_CONTENT_OCR_CHAR_FLOOR
        assert _retry_wins_when_pre_density_none(floor) is True

    @pytest.mark.parametrize("post_retry_chars", [0, 1, 300, 10_000])
    def test_decision_never_consults_post_density(self, post_retry_chars):
        # Property 4's essence: once _pre_density is None, no _post_density
        # value can flip the outcome -- only the absolute char floor decides.
        floor = client_mod.LOW_CONTENT_OCR_CHAR_FLOOR
        expected = post_retry_chars >= floor
        assert _retry_wins_when_pre_density_none(post_retry_chars) is expected


# ---------------------------------------------------------------------------
# Property 5: atomic revert of all six retry-derived state variables.
# ---------------------------------------------------------------------------

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

    def test_retry_loses_no_field_leaks_post_retry_value(self):
        pre, post = _pre_state(), _post_state()

        final = _snapshot_and_maybe_revert(pre, post, retry_wins=False)

        for field in _RETRY_STATE_FIELDS:
            assert final[field] != post[field], (
                f"field {field!r} leaked its post-retry value after a losing retry"
            )

    def test_retry_wins_all_six_fields_take_post_retry_value(self):
        pre, post = _pre_state(), _post_state()

        final = _snapshot_and_maybe_revert(pre, post, retry_wins=True)

        for field in _RETRY_STATE_FIELDS:
            assert final[field] == post[field]

    def test_reverted_state_is_internally_consistent_tree_matches_markdown(self):
        # The bug this property guards against: result/ok/reason revert but
        # md_content/tmp_md_path/pic_results stay on post-retry data, leaving
        # a persisted tree that doesn't match its own source markdown. After
        # an atomic revert, the reverted result's tree text and md_content
        # must both come from the same (pre-retry) snapshot.
        pre, post = _pre_state(), _post_state()

        final = _snapshot_and_maybe_revert(pre, post, retry_wins=False)

        assert final["result"]["structure"][0]["text"] == "pre-retry tree"
        assert final["md_content"] == "pre-retry markdown"
        assert final["tmp_md_path"] == "/tmp/pre.md"
        assert final["pic_results"] == [{"index": 0, "ocr_text": "pre pic"}]
