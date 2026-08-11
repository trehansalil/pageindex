"""RFC-022 B2: image file routing + gate ordering (two-part fix).

Validates Design Properties 3-4 (design-rfc022-run5-verdict-bugfixes.md):
  Property 3 - extension routing: a file whose extension is in _IMAGE_EXTS
  gets content_class="image_standalone" regardless of block-role composition.
  Property 4 - QF2a gate ordering: max_leaf_ratio > 0.75 hard-FAIL now fires
  AFTER image_standalone routing but BEFORE image-enrichment rescue.  A 100%
  single-leaf tree can no longer PASS via enrichment rescue.

The B2-A override landed in client.py as
`apply_image_ext_content_class_override` (RFC-033 D7); this file calls it
directly. It previously mirrored the conditional locally, which is why the
override's total absence from client.py went undetected until Run-15 — do not
reintroduce a mirrored predicate.
"""

from pageindex_mcp import client as client_module
from pageindex_mcp.client import apply_image_ext_content_class_override
from pageindex_mcp.helpers import _classify_image_verdict, classify_verdict


def _apply_extension_override(content_class: str, ext: str) -> str:
    # B2-A (RFC-022 / RFC-033 D7): the real client.py override.
    return apply_image_ext_content_class_override(ext, content_class)


def _single_leaf_tree(size: int = 1000) -> list:
    """One top-level leaf -> max_leaf_ratio == 1.0 (> 0.75 hard-FAIL threshold)."""
    return [{"title": "", "text": "x" * size, "nodes": []}]


def _multi_node_tree() -> list:
    """Two children -> max_leaf_ratio ~0.60 (below 0.75 ceiling, above 0.30 pass)."""
    return [
        {"node_id": "1", "title": "A", "text": "x" * 600, "nodes": []},
        {"node_id": "2", "title": "B", "text": "y" * 400, "nodes": []},
    ]


def test_jpg_extension_sets_image_standalone():
    content_class = _apply_extension_override("flat_prose", ".jpg")
    assert content_class == "image_standalone"


def test_classify_image_verdict_full_ratio_passes():
    assert _classify_image_verdict(1.0) == ("PASS", "image_enrichment_complete")


def test_classify_image_verdict_none_fails():
    assert _classify_image_verdict(None) == ("FAIL", "no_image_enrichment")


def test_image_enrichment_rescue_overrides_max_leaf_ratio():
    """Image-enrichment rescue runs before max_leaf_ratio gate because flat
    image-enriched documents are expected to have single-leaf structure
    (max_leaf_ratio=1.0).  Without the rescue, every image-enriched flat
    doc would hard-FAIL on structure alone."""
    structure = _single_leaf_tree()
    verdict, reason = classify_verdict(structure, "flat_prose", None, image_enrichment_ratio=0.9)
    assert verdict == "PASS"
    assert reason == "image_enrichment_promoted"


def test_image_enrichment_rescue_works_below_hard_fail_ceiling():
    """When max_leaf_ratio is below 0.75, image-enrichment rescue promotes."""
    structure = _multi_node_tree()
    verdict, reason = classify_verdict(structure, "flat_prose", None, image_enrichment_ratio=0.9)
    assert (verdict, reason) == ("PASS", "image_enrichment_promoted")


def test_non_image_enriched_doc_still_fails_on_max_leaf_ratio():
    structure = _single_leaf_tree()
    verdict, reason = classify_verdict(structure, "flat_prose", None, image_enrichment_ratio=None)
    assert verdict == "FAIL"
    assert reason.startswith("max_leaf_ratio=")


def test_pipeline_disabled_falls_back_to_flat_path(monkeypatch):
    monkeypatch.setattr(client_module, "_IMAGE_STANDALONE_PIPELINE_ENABLED", False)
    content_class = _apply_extension_override("flat_prose", ".jpg")
    assert content_class == "flat_prose"
    # Even with the override disabled, the enrichment rescue gate (B2-B) is
    # defense-in-depth and still promotes a well-enriched flat doc — but only
    # when max_leaf_ratio is below the 0.75 hard-FAIL ceiling.
    structure = _multi_node_tree()
    verdict, reason = classify_verdict(structure, content_class, None, image_enrichment_ratio=0.9)
    assert (verdict, reason) == ("PASS", "image_enrichment_promoted")
