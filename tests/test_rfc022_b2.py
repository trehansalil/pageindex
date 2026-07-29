"""RFC-022 B2: image file routing + gate ordering (two-part fix).

Validates Design Properties 3-4 (design-rfc022-run5-verdict-bugfixes.md):
  Property 3 - extension routing: a file whose extension is in _IMAGE_EXTS
  gets content_class="image_standalone" regardless of block-role composition.
  Property 4 - QF2a gate ordering: the image_enrichment_promoted rescue gate
  fires BEFORE the max_leaf_ratio > 0.75 hard-FAIL.

`_apply_extension_override` below mirrors the B2-A override logic as
specified by RFC-022 / task 2.1 (to be placed inline in client.py's
`index()` after `route_and_extract_flat`). NOTE: as of this test's
writing, client.py does NOT yet contain that override (task 2.1 is
marked done in the tasks file but the code is absent) — only the
all(role=="image") check at client.py:1012-1018 exists. These tests
validate the specified override logic and the landed helpers.py gate
hoist; end-to-end Property 3 behavior requires task 2.1 to land.
"""

from pageindex_mcp.client import _IMAGE_EXTS
from pageindex_mcp.helpers import _classify_image_verdict, classify_verdict


def _apply_extension_override(content_class: str, ext: str, pipeline_enabled: bool = True) -> str:
    # B2-A (RFC-022): mirrors client.py's post-route_and_extract_flat override.
    if pipeline_enabled and ext in _IMAGE_EXTS:
        content_class = "image_standalone"
    return content_class


def _single_leaf_tree(size: int = 1000) -> list:
    """One top-level leaf -> max_leaf_ratio == 1.0 (> 0.75 hard-FAIL threshold)."""
    return [{"title": "", "text": "x" * size, "nodes": []}]


def test_jpg_extension_sets_image_standalone():
    content_class = _apply_extension_override("flat_prose", ".jpg")
    assert content_class == "image_standalone"


def test_classify_image_verdict_full_ratio_passes():
    assert _classify_image_verdict(1.0) == ("PASS", "image_enrichment_complete")


def test_classify_image_verdict_none_fails():
    assert _classify_image_verdict(None) == ("FAIL", "no_image_enrichment")


def test_hoisted_qf2a_promotes_despite_high_leaf_ratio():
    structure = _single_leaf_tree()
    verdict, reason = classify_verdict(structure, "flat_prose", None, image_enrichment_ratio=0.9)
    assert (verdict, reason) == ("PASS", "image_enrichment_promoted")


def test_non_image_enriched_doc_still_fails_on_max_leaf_ratio():
    structure = _single_leaf_tree()
    verdict, reason = classify_verdict(structure, "flat_prose", None, image_enrichment_ratio=None)
    assert verdict == "FAIL"
    assert reason.startswith("max_leaf_ratio=")


def test_pipeline_disabled_falls_back_to_flat_path():
    content_class = _apply_extension_override("flat_prose", ".jpg", pipeline_enabled=False)
    assert content_class == "flat_prose"
    # Even with the override disabled, the hoisted QF2a rescue gate (B2-B) is
    # defense-in-depth and still promotes a well-enriched flat doc.
    structure = _single_leaf_tree()
    verdict, reason = classify_verdict(structure, content_class, None, image_enrichment_ratio=0.9)
    assert (verdict, reason) == ("PASS", "image_enrichment_promoted")
