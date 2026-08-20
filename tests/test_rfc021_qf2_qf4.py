"""RFC-021 run-4 verdict quickfixes: unit tests for QF2 (a/b/c) and QF4.

QF2a — image-enrichment promotion for flat docs with rich picture-OCR content.
QF2b — PASS_MAX_LEAF_RATIO relaxation (0.15 -> 0.17, env-overridable).
QF2c — small-doc exemption for well-formed tiny flat_* docs.
QF4  — windowed garble ratio (`_garble_ratio`) and its wiring into
       `classify_verdict` via GARBLE_WINDOW_RATIO_THRESHOLD.

All tree constructions below were verified against the live implementation
(uv run python -c ...) before being pinned into assertions, since
`_tree_max_leaf_ratio`'s leaf-only-chars denominator makes hand-computed
ratios easy to get subtly wrong (e.g. a single-leaf tree always has
ratio == 1.0, and two equally-sized leaves can never produce a ratio below
0.5 -- see the `_shared_root_tree` docstring below for the shape needed to
hit an arbitrary target ratio).
"""

import os
from unittest.mock import patch

import pytest

from pageindex_mcp.config import reset_pipeline_config
from pageindex_mcp.helpers import (
    BULK_PROFILE,
    _flatten_tree_text,
    _garble_ratio,
    check_garble,
    classify_verdict,
    garble_prongs,
)
from tests.conftest import filler_text

# ── synthetic tree builders ─────────────────────────────────────────────────


def _shared_root_tree(leaf_sizes: list[int], corrupt_first: bool = False) -> list:
    """One root node wrapping N leaf children (depth=2, node_count=N+1).

    max_leaf_ratio = max(leaf_sizes) / sum(leaf_sizes) exactly, since titles
    are left empty and only `text` lengths contribute chars. Pass
    `corrupt_first=True` to splice a trailing null byte into the first leaf
    (same length) to force garble detection True via the null-byte prong.
    """
    leaves = []
    for i, size in enumerate(leaf_sizes):
        text = "x" * size
        if corrupt_first and i == 0:
            text = text[:-1] + "\x00"
        leaves.append({"title": "", "text": text, "nodes": []})
    return [{"title": "", "text": "", "nodes": leaves}]


def _make_tree(leaf_sizes: list[int], depth: int = 2) -> list:
    """Same shape as tests/test_verdict_d1.py's `_make_tree`: each leaf gets
    its own chain of (depth - 1) empty parent wrappers, so node_count ==
    depth * len(leaf_sizes) and depth == the given `depth`.
    """
    trees = []
    for idx, size in enumerate(leaf_sizes):
        leaf = {"title": "", "text": filler_text(size, idx), "nodes": []}
        node = leaf
        for _ in range(depth - 1):
            node = {"title": "", "text": "", "nodes": [node]}
        trees.append(node)
    return trees


def _diverse_words(n: int) -> str:
    """n space-separated, all-unique tokens -- clean text that can never trip
    the >30% token-repetition garble heuristic no matter how long it is."""
    return " ".join(f"word{i}" for i in range(n))


# ── QF2a: image-enrichment promotion ────────────────────────────────────────


def _two_leaf_flat_tree() -> list:
    """2 equal leaves under a shared root -> max_leaf_ratio == 0.5 exactly
    (base PASS threshold 0.17 and every category promotion's 0.15/0.17
    thresholds all reject 0.5), so the only way to PASS is QF2a itself."""
    return _shared_root_tree([500, 500])


def test_qf2a_flat_prose_high_enrichment_passes():
    tree = _two_leaf_flat_tree()
    verdict, reason = classify_verdict(tree, "flat_prose", None, image_enrichment_ratio=1.0)
    assert (verdict, reason) == ("PASS", "image_enrichment_promoted")


def test_qf2a_low_enrichment_no_promotion():
    tree = _two_leaf_flat_tree()
    verdict, reason = classify_verdict(tree, "flat_prose", None, image_enrichment_ratio=0.5)
    assert verdict == "MARGINAL"
    assert reason == "leaf_concentration=0.50"


def test_qf2a_non_flat_no_promotion():
    # content_class="ocr_clean" is neither "flat_prose" nor "flat_mixed",
    # so QF2a's promotion never triggers even at ratio=1.0.
    tree = _two_leaf_flat_tree()
    verdict, reason = classify_verdict(tree, "ocr_clean", None, image_enrichment_ratio=1.0)
    assert verdict == "MARGINAL"
    assert reason == "leaf_concentration=0.50"


def test_qf2a_none_ratio_no_change():
    tree = _two_leaf_flat_tree()
    verdict, reason = classify_verdict(tree, "flat_prose", None, image_enrichment_ratio=None)
    assert verdict == "MARGINAL"
    assert reason == "leaf_concentration=0.50"


def test_qf2a_threshold_boundary():
    tree = _two_leaf_flat_tree()
    below = classify_verdict(tree, "flat_prose", None, image_enrichment_ratio=0.79)
    at = classify_verdict(tree, "flat_prose", None, image_enrichment_ratio=0.80)
    assert below[0] == "MARGINAL"
    assert at == ("PASS", "image_enrichment_promoted")


# ── QF2b: PASS_MAX_LEAF_RATIO relaxation ────────────────────────────────────


def test_qf2b_ratio_016_passes():
    # 0.16 sits between the old (0.15) and new (0.17) thresholds.
    # depth=4 (rather than 2) so RFC-036 D6's complexity-proportional
    # depth-adequacy check (node_count=340 -> expected_min_depth=4) doesn't
    # cap this leaf-ratio-focused test at MARGINAL for an unrelated reason.
    tree = _make_tree([160] + [10] * 84, depth=4)
    verdict, reason = classify_verdict(tree, "default", None)
    assert (verdict, reason) == ("PASS", "")


def test_qf2b_ratio_018_marginal():
    # RFC-026 D0 widened PASS_MAX_LEAF_RATIO 0.20 -> 0.30, so the old 0.21
    # ratio now clears the base gate. Recalibrated to 0.35, just above the
    # new threshold -> stays MARGINAL. content_class "default" also fails
    # the (unchanged, 0.17) cat_c promotion threshold, so no
    # category-promotion path masks the base-gate result.
    tree = _make_tree([350] + [10] * 65, depth=2)
    verdict, reason = classify_verdict(tree, "default", None)
    assert verdict == "MARGINAL"
    assert reason == "leaf_concentration=0.35"


def test_qf2b_env_var_override():
    # At ratio=0.16 the base PASS gate fires under the default 0.17
    # threshold (see test_qf2b_ratio_016_passes). Overriding
    # PASS_MAX_LEAF_RATIO back down to 0.15 must defeat that base gate.
    # content_class="ocr_rescued" is used so the only category-promotion
    # path available (cat_a) *also* requires ratio<0.15, so the override
    # is what pushes this doc all the way down to MARGINAL.
    tree = _make_tree([160] + [10] * 84, depth=2)
    with patch.dict(os.environ, {"PASS_MAX_LEAF_RATIO": "0.15"}):
        reset_pipeline_config()
        verdict, reason = classify_verdict(tree, "ocr_rescued", None)
    assert verdict == "MARGINAL"
    assert reason == "leaf_concentration=0.16"


def test_qf2b_existing_pass_docs_still_pass():
    # Docs that already passed under the old 0.15 threshold (ratio=0.05
    # here) must keep passing under the relaxed 0.17 default, with no env
    # override needed.
    tree = _shared_root_tree([50] * 20)
    verdict, reason = classify_verdict(tree, "default", None)
    assert (verdict, reason) == ("PASS", "")


# ── QF2c: small-doc exemption ───────────────────────────────────────────────
#
# NOTE: for content_class.startswith("flat_"), cat_b_promoted already PASSes
# any doc with node_count>=3 and max_leaf_ratio < CATEGORY_BC_PROMOTION_
# THRESHOLD (0.17), which would preempt QF2c for any ratio below 0.17 and
# node_count>=3. To actually exercise the QF2c-specific exemption (rather
# than always landing on cat_b_promoted first), these trees use
# max_leaf_ratio=0.18 -- above cat_b's 0.17 gate, but still below QF2c's own
# 0.20 gate.


def test_qf2c_small_doc_promoted():
    # node_count=8 (root + 7 leaves), ratio=0.18, flat_text len=1200 (in
    # [100, 15000)), clean. RFC-023 D10 widened the base
    # PASS_MAX_LEAF_RATIO gate to 0.20 (same as QF2c's own threshold), so
    # PASS_MAX_LEAF_RATIO is forced low here to reach the base MARGINAL
    # tier and isolate the QF2c small-doc-exemption path specifically.
    with patch.dict(os.environ, {"PASS_MAX_LEAF_RATIO": "0.10"}):
        reset_pipeline_config()
        tree = _shared_root_tree([216, 164, 164, 164, 164, 164, 164])
        verdict, reason = classify_verdict(tree, "flat_prose", None)
    reset_pipeline_config()
    assert (verdict, reason) == ("PASS", "small_doc_promoted")


def test_qf2c_too_many_nodes():
    # node_count=15 (root + 14 leaves, >10) -> exemption denied even though
    # ratio (0.18) and length (13000) are both in range. PASS_MAX_LEAF_RATIO
    # forced low (see test_qf2c_small_doc_promoted) so the base gate doesn't
    # mask the node-count rejection.
    with patch.dict(os.environ, {"PASS_MAX_LEAF_RATIO": "0.10"}):
        reset_pipeline_config()
        tree = _shared_root_tree([2340] + [820] * 13)
        verdict, reason = classify_verdict(tree, "flat_prose", None)
    assert verdict == "MARGINAL"
    assert reason == "leaf_concentration=0.18"


def test_qf2c_too_long_text():
    # node_count=10 (in range), ratio=0.18 (in range), but flat_text
    # len=16000 (>=15000) -> exemption denied. PASS_MAX_LEAF_RATIO forced
    # low (see test_qf2c_small_doc_promoted) so the base gate doesn't mask
    # the text-length rejection.
    with patch.dict(os.environ, {"PASS_MAX_LEAF_RATIO": "0.10"}):
        reset_pipeline_config()
        tree = _shared_root_tree([2880] + [1640] * 8)
        verdict, reason = classify_verdict(tree, "flat_prose", None)
    assert verdict == "MARGINAL"
    assert reason == "leaf_concentration=0.18"


def test_qf2c_garbled_no_exemption():
    # Same node_count/ratio/length as test_qf2c_small_doc_promoted, but the
    # first leaf has a spliced null byte -> garble detection fires, garble
    # ratio saturates to 1.0 (>= default 0.05 threshold) -> effectively
    # garbled -> QF2c's `not effectively_garbled` guard denies exemption.
    tree = _shared_root_tree([216, 164, 164, 164, 164, 164, 164], corrupt_first=True)
    verdict, reason = classify_verdict(tree, "flat_prose", None)
    assert verdict == "MARGINAL"
    assert reason == "garbling(ratio=1.00)"


def test_qf2c_disabled_via_env():
    # PASS_MAX_LEAF_RATIO forced low (see test_qf2c_small_doc_promoted) so
    # the base gate doesn't mask the disabled-flag rejection.
    tree = _shared_root_tree([216, 164, 164, 164, 164, 164, 164])
    with patch.dict(
        os.environ,
        {"SMALL_DOC_PROMOTION_ENABLED": "false", "PASS_MAX_LEAF_RATIO": "0.10"},
    ):
        reset_pipeline_config()
        verdict, reason = classify_verdict(tree, "flat_prose", None)
    assert verdict == "MARGINAL"
    assert reason == "leaf_concentration=0.18"


# ── QF4: windowed garble ratio (`_garble_ratio` direct tests) ──────────────


def test_garble_ratio_clean_text():
    text = "The quick brown fox jumps over the lazy dog. " * 50  # 2250 chars
    assert len(text) > 2000
    assert _garble_ratio(text) == 0.0


def test_garble_ratio_fully_garbled():
    text = "" * 3000  # PUA ratio 100% -> both full and windowed flag it
    assert _garble_ratio(text) == 1.0


def test_garble_ratio_partially_garbled():
    # A single 2000-char window saturated with one repeated garbled token
    # ("zzzzz " * 400 == 2400 chars), followed by ~14.6k chars of fully
    # diverse, non-repeating tokens. The repeated token is diluted below the
    # 30% full-text repetition threshold by the diverse tail (so the
    # full-text check does NOT flag it), but the leading window is >99%
    # "zzzzz" tokens and independently trips the windowed check -> the
    # overall ratio is a small positive fraction, not 0 and not 1.
    garbled_chunk = "zzzzz " * 400
    clean_chunk = _diverse_words(2000)
    text = garbled_chunk + clean_chunk

    assert check_garble(text, expected_script=None, profile=BULK_PROFILE) is False  # full-text check alone misses it
    ratio = _garble_ratio(text)
    assert 0.0 < ratio < 0.5


def test_garble_ratio_short_text_fullcheck():
    # len(text) <= 2000 short-circuits _garble_ratio to return the full-text
    # check verbatim, with no windowing at all.
    short_clean = _diverse_words(150)
    assert len(short_clean) <= 2000
    assert _garble_ratio(short_clean) == 0.0

    short_garbled = ("clean prose text here today " * 30) + ("\x00" * 5)
    assert len(short_garbled) <= 2000
    assert _garble_ratio(short_garbled) == 1.0


def test_garble_ratio_additive_only():
    # Same construction as the partial-garbling test: the full-text check
    # alone reports clean (0.0), but _garble_ratio's max(full, window)
    # additive-only combination still surfaces the windowed hit as > 0.0 --
    # i.e. windowing can only ever raise the ratio, never suppress a
    # full-text-detected garble.
    garbled_chunk = "zzzzz " * 400
    clean_chunk = _diverse_words(2000)
    text = garbled_chunk + clean_chunk

    full_only = 1.0 if check_garble(text, expected_script=None, profile=BULK_PROFILE) else 0.0
    assert full_only == 0.0
    assert _garble_ratio(text) > full_only


def test_garble_ratio_sparse_mojibake_preserved():
    # Sparse Arabic-Latin-Arabic glued mojibake (RFC-015 D8): 5 glued
    # fragments among 100 space-separated Arabic tokens (8 distinct words,
    # so no token exceeds the 30% repetition threshold on its own) ->
    # _is_garbled_blob's bulk heuristics (PUA/digit/repetition/control-char)
    # all miss it, but _has_sparse_mojibake's dedicated glued-fragment regex
    # still catches it (5/100 = 5% > its 2% threshold) and _garble_ratio's
    # full-text prong ORs it in.
    words = ["كتاب", "مدرسة", "قلم", "طالب", "معلم", "بيت", "شجرة", "سيارة"]
    tokens = []
    for i in range(100):
        if i % 20 == 0:
            tokens.append(f"{words[i % 8]}AB{i}ك")  # glued Arabic-Latin-Arabic
        else:
            tokens.append(words[i % 8])
    text = " ".join(tokens)

    # Zone-1 consolidation: check_garble with BULK_PROFILE now includes
    # _has_sparse_mojibake, so it catches this text too.
    assert check_garble(text, expected_script=None, profile=BULK_PROFILE) is True
    assert _garble_ratio(text) == 1.0


def test_classify_verdict_garble_ratio_threshold():
    # A doc with one leaf carrying a spliced null byte is garbled (binary
    # gate fires), which saturates _garble_ratio to 1.0 (see
    # test_qf2c_garbled_no_exemption). Under the default
    # GARBLE_WINDOW_RATIO_THRESHOLD (0.05), 1.0 clears it and the doc is
    # "effectively garbled" -> MARGINAL with a garbling reason. Raising the
    # threshold env var above 1.0 makes that same 1.0 ratio fall below the
    # bar, flipping effectively_garbled to False and letting the (otherwise
    # clean) tree PASS outright with no garbling mention in the reason.
    leaves = [{"title": "", "text": "x" * 100, "nodes": []} for _ in range(10)]
    leaves[0]["text"] = "x" * 99 + "\x00"
    tree = [{"title": "", "text": "", "nodes": leaves}]

    assert check_garble(_flatten_tree_text(tree), expected_script=None, profile=BULK_PROFILE) is True

    verdict, reason = classify_verdict(tree, "default", None)
    assert verdict == "MARGINAL"
    assert reason == "garbling(ratio=1.00)"

    with patch.dict(os.environ, {"GARBLE_WINDOW_RATIO_THRESHOLD": "2.0"}):
        reset_pipeline_config()
        verdict, reason = classify_verdict(tree, "default", None)
    assert (verdict, reason) == ("PASS", "")
    assert "garbling" not in reason
