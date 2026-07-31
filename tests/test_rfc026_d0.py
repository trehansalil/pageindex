"""Tests for RFC-026 Task 1.1 (D0): hard FAIL floor for zero-content documents.

Validates Design Property 1: for any input to ``classify_verdict()`` where
``node_count == 0`` or ``total_chars == 0``, the return value is always
``("FAIL", "zero_content")``, regardless of ``content_class``,
``image_enrichment_ratio``, or ``prior_verdict``. No PASS or MARGINAL verdict
is reachable for a zero-content document, and the check must fire before the
``image_enrichment_promoted`` branch (D1).
"""

from pageindex_mcp.helpers import classify_verdict


class TestZeroContentFailFloor:
    def test_zero_node_count_fails_zero_content(self):
        """An empty structure (node_count == 0) must FAIL zero_content even
        when content_class/image_enrichment_ratio would otherwise promote it
        via the image_enrichment_promoted branch."""
        structure = []
        verdict, reason = classify_verdict(
            structure, "flat_prose", None, image_enrichment_ratio=0.9
        )
        assert (verdict, reason) == ("FAIL", "zero_content")

    def test_zero_total_chars_fails_zero_content(self):
        """A structure with nodes but no text/title anywhere (total_chars == 0)
        must FAIL zero_content, same as node_count == 0."""
        structure = [
            {
                "node_id": "1",
                "title": "",
                "text": "",
                "nodes": [
                    {"node_id": "2", "title": "", "text": "", "nodes": []},
                ],
            },
        ]
        verdict, reason = classify_verdict(
            structure, "flat_prose", None, image_enrichment_ratio=0.9
        )
        assert (verdict, reason) == ("FAIL", "zero_content")

    def test_zero_content_fires_before_image_enrichment_branch(self):
        """A zero-content doc with content_class/image_enrichment_ratio that
        would otherwise hit the image_enrichment_promoted PASS path (D1) must
        still resolve to FAIL zero_content -- the D0 gate runs first and is
        unconditional."""
        structure = []
        verdict, reason = classify_verdict(
            structure, "flat_mixed", None, image_enrichment_ratio=1.0
        )
        assert verdict == "FAIL"
        assert reason == "zero_content"
        assert reason != "image_enrichment_promoted"

    def test_non_zero_content_regression_guard(self):
        """A normal, non-empty tree must NOT be swept into the zero_content
        FAIL floor -- the D0 gate is additive and must not affect documents
        with actual content."""
        structure = [
            {
                "node_id": "n1",
                "title": "Section A",
                "text": "x" * 50,
                "nodes": [
                    {"node_id": "n1.1", "title": "A.1", "text": "y" * 50, "nodes": []},
                ],
            },
            {"node_id": "n2", "title": "Section B", "text": "z" * 50, "nodes": []},
            {"node_id": "n3", "title": "Section C", "text": "w" * 50, "nodes": []},
        ]
        verdict, reason = classify_verdict(structure, "", None)
        assert reason != "zero_content"
        assert verdict != "FAIL" or reason != "zero_content"
